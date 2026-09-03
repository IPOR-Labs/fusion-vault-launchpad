"""Step 01: FusionFactory.clone — deploys the full vault stack."""
from __future__ import annotations

from dataclasses import asdict

from web3 import Web3

NAME = "01_clone"


def _owner_model_banner(cfg, deployer, appointed_owner, keep_deployer_owner):
    """Print the chosen ownership model + guard against a mis-specified one."""
    print(f"[{NAME}] ownership model:")
    if keep_deployer_owner:
        print(f"[{NAME}]   MODE A — deployer kept as TEMPORARY owner (agent can harden autonomously)")
        print(f"[{NAME}]     clone owner / temporary OWNER: {deployer}")
        print(f"[{NAME}]     appointed (final) OWNER:       {appointed_owner}"
              + ("  (== deployer)" if appointed_owner == deployer else "  (via roles.grants)"))
        print(f"[{NAME}]     -> the deployer's OWNER_ROLE is renounced at the END of hardening,")
        print(f"[{NAME}]        after the appointed owner is confirmed to hold OWNER on-chain.")
        if appointed_owner != deployer:
            grants = cfg.raw.get("roles", {}).get("grants", [])
            has_owner_grant = any(
                g.get("role") == "OWNER_ROLE"
                and Web3.to_checksum_address(g["account"]) == appointed_owner
                for g in grants
            )
            if not has_owner_grant:
                print(f"[{NAME}]     WARNING: appointed owner {appointed_owner} has no OWNER_ROLE grant "
                      f"in roles.grants — it will NOT become OWNER, and the deployer's end-of-hardening "
                      f"renounce would leave the vault without an independent OWNER. Add an OWNER_ROLE grant.")
    else:
        print(f"[{NAME}]   MODE B — appointed owner only (no temporary deployer owner; hardening is manual)")
        print(f"[{NAME}]     clone owner / OWNER: {appointed_owner}"
              + ("  (== deployer)" if appointed_owner == deployer else ""))
        if appointed_owner != deployer:
            print(f"[{NAME}]     NOTE: the deployer ({deployer}) is not the owner — the config pipeline "
                  f"must be signed by the appointed owner's key, and hardening is owner-signed (manual).")


def run(cfg, deploy_ctx, session, instance_holder, state, broadcast):
    if state.fusion_instance:
        print(f"[{NAME}] already cloned at {state.fusion_instance['plasma_vault']} — skipping")
        return state.fusion_instance

    vault = cfg.raw["vault"]
    deployer = Web3.to_checksum_address(session.signer)
    # The APPOINTED owner is the vault's intended FINAL OWNER (granted OWNER_ROLE via
    # roles.grants / s11). Defaults to the deployer when no override is given.
    appointed_owner = Web3.to_checksum_address(vault.get("initial_owner_override") or session.signer)

    # Ownership model — the curator's choice (schema: vault.deployer_temporary_owner):
    #   Mode A (true):  clone under the DEPLOYER, so it holds a TEMPORARY OWNER_ROLE
    #                   alongside the appointed owner. The agent can then run the full
    #                   configuration AND hardening pipeline autonomously and renounce
    #                   its own OWNER as the last hardening step.
    #   Mode B (false): clone under the appointed owner only — no temporary deployer
    #                   owner; hardening becomes a manual (owner-signed) process.
    keep_deployer_owner = bool(vault.get("deployer_temporary_owner"))
    if vault.get("deployer_temporary_owner") is None:
        print(f"[{NAME}] NOTE: vault.deployer_temporary_owner is not set — defaulting to MODE B "
              f"(manual, owner-signed hardening).\n[{NAME}]       This is meant to be an EXPLICIT "
              f"choice at intake: true = Mode A (agent hardens autonomously, then renounces "
              f"all roles), false = Mode B. Set it to silence this note.")
    owner = deployer if keep_deployer_owner else appointed_owner
    _owner_model_banner(cfg, deployer, appointed_owner, keep_deployer_owner)

    args = dict(
        asset_name=vault["name"],
        asset_symbol=vault["symbol"],
        underlying_token=Web3.to_checksum_address(vault["underlying"]),
        redemption_delay_seconds=int(vault["redemption_delay_seconds"]),
        owner=owner,
        dao_fee_package_index=int(vault["dao_fee_package_index"]),
    )

    # Preview via eth_call to learn CREATE2 deterministic addresses.
    preview = session.factory.clone(**args).call()
    addrs = {
        "plasma_vault": preview.plasma_vault,
        "access_manager": preview.access_manager,
        "fee_manager": preview.fee_manager,
        "rewards_manager": preview.rewards_manager,
        "withdraw_manager": preview.withdraw_manager,
        "context_manager": preview.context_manager,
        "price_manager": preview.price_manager,
        "initial_owner": preview.initial_owner,
    }
    print(f"[{NAME}] preview plasma_vault={addrs['plasma_vault']}")
    print(f"[{NAME}]   access_manager={addrs['access_manager']}")
    print(f"[{NAME}]   withdraw_manager={addrs['withdraw_manager']}")
    print(f"[{NAME}]   fee_manager={addrs['fee_manager']}")
    print(f"[{NAME}]   initial_owner={addrs['initial_owner']}")

    rec = session.recorder.add(
        NAME, action="clone", key=vault["symbol"], function="clone(...)",
        target=str(session.factory.address) if hasattr(session.factory, "address") else None,
        args={k: str(v) for k, v in args.items()},
    )

    # Populate the in-memory instance for downstream steps in BOTH modes. In a
    # dry-run this is never persisted (the orchestrator only saves state on
    # --broadcast), so it cannot make a later real broadcast skip the clone.
    state.fusion_instance = addrs

    if not broadcast:
        return addrs

    receipt = session.factory.clone(**args).send()
    tx_hash = receipt["transactionHash"].hex()
    print(f"[{NAME}] tx {tx_hash} gas_used={receipt['gasUsed']}")
    rec.executed = True
    rec.tx_hash = tx_hash
    rec.gas_used = int(receipt["gasUsed"])
    rec.note = "deployed FusionInstance"
    # Persist the ownership model so hardening knows whether a temporary deployer
    # OWNER exists that it must renounce at the end (Mode A), or not (Mode B).
    owner_model = {
        "clone_owner": owner,
        "appointed_owner": appointed_owner,
        "deployer": deployer,
        "deployer_temporary_owner": keep_deployer_owner,
        "renounce_deployer_owner_at_hardening_end": keep_deployer_owner and appointed_owner != deployer,
    }
    state.record(NAME, tx_hashes=[tx_hash], notes={**addrs, "owner_model": owner_model})
    return addrs
