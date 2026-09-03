"""Step 01c: set the vault total-supply cap — DECIMALS-SAFE.

⚠️ The cap is on total SHARES, whose decimals = underlying decimals + the vault's
decimals OFFSET (e.g. cbBTC 8 + offset 2 = 10 share decimals). Computing the raw
cap from the *underlying* decimals is a classic off-by-10^offset error — it is
exactly what once shipped a vault with a 0.1 cbBTC cap instead of 10 (2026-05-22).

So this step reads the DEPLOYED vault's own `decimals()` and converts a
human-readable underlying amount (`vault.total_supply_cap_underlying`, e.g. "10")
into raw share units. A raw `vault.total_supply_cap` is still honored as an
explicit escape hatch, but the human field is preferred and recommended.

Runs after 01b_bootstrap_roles (needs the ATOMIST role granted to the deployer).
"""
from __future__ import annotations

from decimal import Decimal

from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion.core.plasma_vault import PlasmaVault

NAME = "01c_total_supply_cap"


def _vault_decimals(session, vault_addr) -> int:
    raw = session.ctx.call(
        Web3.to_checksum_address(vault_addr),
        function_signature_to_4byte_selector("decimals()"),
    )
    return int.from_bytes(bytes(raw), "big")


def run(cfg, deploy_ctx, session, instance, state, broadcast):
    if state.has_step(NAME):
        print(f"[{NAME}] already done — skipping")
        return

    vault = cfg.raw["vault"]
    human = vault.get("total_supply_cap_underlying")
    raw_cap = vault.get("total_supply_cap")
    if human in (None, "") and raw_cap in (None, ""):
        print(f"[{NAME}] no cap configured — vault left uncapped")
        if broadcast:
            state.record(NAME)
        return

    rec = session.recorder.add(
        NAME, action="setTotalSupplyCap", key="cap",
        target=instance["plasma_vault"], function="setTotalSupplyCap(uint256)",
        args=({"cap_underlying": str(human)} if human not in (None, "")
              else {"cap_raw_shares": str(raw_cap)}),
    )

    if not broadcast:
        if human not in (None, ""):
            print(f"[{NAME}] will set cap = {human} {vault['symbol']} underlying "
                  f"(raw computed from the vault's own decimals() at broadcast)")
        else:
            print(f"[{NAME}] will set cap = {raw_cap} (raw SHARE units, as given)")
        return

    share_dec = _vault_decimals(session, instance["plasma_vault"])
    if human not in (None, ""):
        cap = int(Decimal(str(human)) * (Decimal(10) ** share_dec))
        print(f"[{NAME}] cap = {human} underlying x 10^{share_dec} (share decimals) = {cap}")
    else:
        cap = int(raw_cap)
        print(f"[{NAME}] cap = {cap} (raw SHARE units; vault share decimals={share_dec})")

    pv = PlasmaVault(session.ctx, instance["plasma_vault"])
    receipt = pv.set_total_supply_cap(cap).send()
    tx_hash = receipt["transactionHash"].hex()
    print(f"[{NAME}] setTotalSupplyCap tx={tx_hash}")
    rec.executed = True
    rec.tx_hash = tx_hash
    rec.gas_used = int(receipt["gasUsed"])
    # Computed results go in `note` (a result field), NOT args — args is the
    # config-determined identity the plan↔run diff matches on.
    rec.note = f"raw_shares={cap} share_decimals={share_dec}"
    state.record(NAME, tx_hashes=[tx_hash],
                 notes={"cap": str(cap), "share_decimals": share_dec})
