"""Step 08: configureInstantWithdrawalFuses."""
from __future__ import annotations

from web3 import Web3

from ipor_fusion.core.plasma_vault import PlasmaVault

from deploy.encoders.substrates import _address_to_bytes32, _to_bytes32
from deploy.fuses import queue_param_problems

NAME = "08_instant_withdrawal"


def _encode_param(param: dict) -> bytes:
    enc = param.get("encoding", "address")
    if enc == "address":
        return _address_to_bytes32(param["value"])
    if enc == "raw_bytes32":
        return _to_bytes32(param["value"])
    if enc == "uint256":
        return int(param["value"]).to_bytes(32, "big")
    raise ValueError(f"unknown instant-withdrawal param encoding: {enc}")


def run(cfg, deploy_ctx, session, instance, state, broadcast):
    if state.has_step(NAME):
        print(f"[{NAME}] already done — skipping")
        return
    problems = queue_param_problems(cfg.raw["instant_withdrawal"]["order"])
    if problems:
        raise ValueError(f"instant-withdrawal queue entries too short for their fuse family: {problems}")
    vault = PlasmaVault(session.ctx, instance["plasma_vault"])
    items = []
    for entry in cfg.raw["instant_withdrawal"]["order"]:
        fuse_addr = deploy_ctx.fuse(entry["fuse"])
        params = [_encode_param(p) for p in entry.get("params", [])]
        items.append((fuse_addr, params))
        print(f"[{NAME}] {entry['fuse']} ({fuse_addr}) params={len(params)}")
    if not items:
        print(f"[{NAME}] no instant-withdrawal fuses configured")
        return
    rec = session.recorder.add(
        NAME, action="configureInstantWithdrawalFuses", key="order",
        target=instance["plasma_vault"], function="configureInstantWithdrawalFuses((address,bytes32[])[])",
        args={"order": [e["fuse"] for e in cfg.raw["instant_withdrawal"]["order"]]},
    )
    if not broadcast:
        return
    receipt = vault.configure_instant_withdrawal_fuses(items).send()
    tx_hash = receipt["transactionHash"].hex()
    print(f"[{NAME}] tx {tx_hash} gas={receipt['gasUsed']}")
    rec.executed = True
    rec.tx_hash = tx_hash
    rec.gas_used = int(receipt["gasUsed"])
    state.record(NAME, tx_hashes=[tx_hash])
