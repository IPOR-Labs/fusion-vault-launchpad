"""Step 10: grant WHITELIST_ROLE to initial accounts (if whitelist enabled)."""
from __future__ import annotations

from ipor_fusion.core.access import AccessManager
from ipor_fusion.config.roles import Roles
from web3 import Web3

NAME = "10_whitelist"


def run(cfg, deploy_ctx, session, instance, state, broadcast):
    if state.has_step(NAME):
        print(f"[{NAME}] already done — skipping")
        return
    wl = cfg.raw["whitelist"]
    if not wl["enabled_at_launch"]:
        print(f"[{NAME}] whitelist disabled at launch — skipping")
        state.record(NAME)
        return
    access = AccessManager(session.ctx, instance["access_manager"])
    delay = int(cfg.raw["roles"].get("execution_delay_seconds", 0))
    tx_hashes = []
    for acc in wl.get("initial_accounts", []):
        addr = Web3.to_checksum_address(acc)
        print(f"[{NAME}] grant WHITELIST_ROLE({Roles.WHITELIST_ROLE}) -> {addr} delay={delay}")
        rec = session.recorder.add(
            NAME, action="grantRole(WHITELIST_ROLE)", key=str(addr),
            target=instance["access_manager"], function="grantRole(uint64,address,uint32)",
            args={"role": "WHITELIST_ROLE", "role_id": int(Roles.WHITELIST_ROLE),
                  "account": str(addr), "delay": delay},
        )
        if not broadcast:
            continue
        receipt = access.grant_role(int(Roles.WHITELIST_ROLE), addr, delay).send()
        tx_hash = receipt["transactionHash"].hex()
        tx_hashes.append(tx_hash)
        rec.executed = True
        rec.tx_hash = tx_hash
        rec.gas_used = int(receipt["gasUsed"])
    if broadcast:
        state.record(NAME, tx_hashes=tx_hashes)
