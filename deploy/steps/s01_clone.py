"""Step 01: FusionFactory.clone — deploys the full vault stack."""
from __future__ import annotations

from dataclasses import asdict

from eth_abi import decode
from web3 import Web3

NAME = "01_clone"


def _call_as(call, sender, web3):
    """`Call.call()` with an explicit `from` (the SDK's eth_call omits it)."""
    raw = web3.eth.call({"to": call.to, "data": call.data, "from": Web3.to_checksum_address(sender)})
    values = tuple(decode(call.output_types, bytes(raw)))
    single = values[0] if len(values) == 1 else values
    return call.decoder(single) if call.decoder is not None else single


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
    # Skip only a clone that was actually sent. A state file can hold preview addresses from a
    # clone that never reached the chain (older runs saved them before sending); drop those.
    if state.fusion_instance and state.has_step(NAME):
        print(f"[{NAME}] already cloned at {state.fusion_instance['plasma_vault']} — skipping")
        return state.fusion_instance
    if state.fusion_instance:
        print(f"[{NAME}] discarding unsent preview addresses {state.fusion_instance['plasma_vault']} — cloning")
        state.fusion_instance = None

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
    if broadcast and owner != deployer:
        # Every configuration step after the clone is signed by this run's signer. If the clone
        # belongs to someone else, those steps cannot succeed and the vault would be left half-built.
        raise RuntimeError(
            f"[{NAME}] refusing to clone: the vault would be owned by {owner}, but this run signs as {deployer}, "
            "which could then not grant any role or configure the vault. Either set vault.deployer_temporary_owner "
            "to true (the deployer owns it during setup and hands OWNER to the appointed owner at the end), or run "
            "the whole deployment signed by the appointed owner's wallet.")

    args = dict(
        asset_name=vault["name"],
        asset_symbol=vault["symbol"],
        underlying_token=Web3.to_checksum_address(vault["underlying"]),
        redemption_delay_seconds=int(vault["redemption_delay_seconds"]),
        owner=owner,
        dao_fee_package_index=int(vault["dao_fee_package_index"]),
    )

    # Preview via eth_call to learn CREATE2 deterministic addresses. The call must come
    # from the deployer: the factory picks the DAO fee package by msg.sender (a business
    # client's own list first), and the SDK's Call.call() sends no `from`, so the preview
    # would check the index against the default packages and revert for a client index.
    preview = _call_as(session.factory.clone(**args), session.signer, session.ctx.web3)
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

    if not broadcast:
        # In-memory only for the dry-run's downstream steps; a dry-run never saves state.
        state.fusion_instance = addrs
        return addrs

    # Set the instance only after the clone is mined: a failed or unsigned send must not leave
    # preview addresses in state, or the next run would skip the clone and configure nothing.
    receipt = session.factory.clone(**args).send()
    state.fusion_instance = addrs
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
