"""Step 11: grant all configured roles via AccessManager.

Same propagation-race handling as 01b: hasRole is read directly via web3
(bypassing the SDK wrapper) and we poll until the grant is visible before
moving on, so consecutive grants whose target's required admin role was just
granted don't trip eth_estimateGas on a stale RPC backend.
"""
from __future__ import annotations

from ipor_fusion.core.access import AccessManager
from web3 import Web3

from deploy.role_helpers import has_role_raw, wait_for_member
from deploy.roles import resolve_role_id

NAME = "11_roles"


def run(cfg, deploy_ctx, session, instance, state, broadcast):
    if state.has_step(NAME):
        print(f"[{NAME}] already done — skipping")
        return
    access = AccessManager(session.ctx, instance["access_manager"])
    web3 = session.ctx.web3
    am_address = Web3.to_checksum_address(instance["access_manager"])
    default_delay = int(cfg.raw["roles"].get("execution_delay_seconds", 0))
    tx_hashes = []
    skipped_existing = 0
    for grant in cfg.raw["roles"]["grants"]:
        role_name = grant["role"]
        if role_name == "WHITELIST_ROLE":
            # covered by s10; skip here unless extra grants beyond initial_accounts
            continue
        role_id = resolve_role_id(role_name)
        addr = Web3.to_checksum_address(grant["account"])
        delay = int(grant.get("execution_delay_seconds") or default_delay)

        rec = session.recorder.add(
            NAME, action="grantRole", key=f"{role_name}->{addr}",
            target=am_address, function="grantRole(uint64,address,uint32)",
            args={"role": role_name, "role_id": role_id, "account": str(addr), "delay": delay},
        )
        if has_role_raw(web3, am_address, role_id, addr):
            skipped_existing += 1
            rec.skipped = True
            rec.note = "account already holds role"
            continue

        print(f"[{NAME}] grant {role_name}({role_id}) -> {addr} delay={delay}")
        if not broadcast:
            continue
        receipt = access.grant_role(role_id, addr, delay).send()
        tx_hash = receipt["transactionHash"].hex()
        tx_hashes.append(tx_hash)
        rec.executed = True
        rec.tx_hash = tx_hash
        rec.gas_used = int(receipt["gasUsed"])
        if not wait_for_member(web3, am_address, role_id, addr):
            print(f"[{NAME}]   warning: {role_name} ({role_id}) -> {addr} not visible after grant")
    if skipped_existing:
        print(f"[{NAME}] skipped {skipped_existing} roles (already granted)")
    if broadcast:
        # every granted account recorded so hardening can probe role holders
        # (hasRole per candidate) without needing an eth_getLogs-capable RPC
        accounts = sorted({Web3.to_checksum_address(g["account"]) for g in cfg.raw["roles"]["grants"]})
        state.record(NAME, tx_hashes=tx_hashes, notes={"accounts": accounts})
