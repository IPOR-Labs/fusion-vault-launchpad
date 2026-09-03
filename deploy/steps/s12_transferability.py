"""Step 12: enableTransferShares if transferability.enabled_at_launch=true.

⚠️ Irreversible (transferability can be enabled, never disabled).
Requires irreversible_ack=true in the JSON.

NOTE: this step previously called convertToPublicVault(), which is the
WHITELIST-DISABLE one-way switch (deposit/mint gate), NOT transferability —
on-chain truth: PlasmaVaultGovernance.enableTransferShares() flips
transfer/transferFrom from TECH_VAULT_TRANSFER_SHARES_ROLE(7) to PUBLIC.
Caught on the weeth-earn fork rehearsal 2026-07-29 (deposit went PUBLIC,
transfer stayed blocked). Whitelist opening belongs to the whitelist step,
never here.
"""
from __future__ import annotations

from eth_utils import function_signature_to_4byte_selector

NAME = "12_transferability"

_SIG = "enableTransferShares()"


def run(cfg, deploy_ctx, session, instance, state, broadcast):
    if state.has_step(NAME):
        print(f"[{NAME}] already done — skipping")
        return
    tr = cfg.raw["transferability"]
    if not tr["enabled_at_launch"]:
        print(f"[{NAME}] transferability disabled at launch — skipping")
        state.record(NAME)
        return
    if not tr.get("irreversible_ack"):
        raise RuntimeError("transferability.enabled_at_launch=true requires irreversible_ack=true")
    vault_addr = instance["plasma_vault"]
    calldata = function_signature_to_4byte_selector(_SIG)
    print(f"[{NAME}] enableTransferShares on {vault_addr} (IRREVERSIBLE)")
    rec = session.recorder.add(
        NAME, action="enableTransferShares", key="transferability",
        target=vault_addr, function=_SIG,
        args={}, note="IRREVERSIBLE",
    )
    if not broadcast:
        return
    receipt = session.ctx.send(vault_addr, calldata)
    tx_hash = receipt["transactionHash"].hex()
    print(f"[{NAME}] tx {tx_hash} gas={receipt['gasUsed']}")
    rec.executed = True
    rec.tx_hash = tx_hash
    rec.gas_used = int(receipt["gasUsed"])
    state.record(NAME, tx_hashes=[tx_hash])
