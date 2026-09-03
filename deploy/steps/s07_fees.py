"""Step 07: FeeManager — supplemental mgmt + perf via updateManagementFee / updatePerformanceFee.

FeeManager signatures (from ipor-fusion repo):
  updateManagementFee((address,uint256)[])
  updatePerformanceFee((address,uint256)[])
"""
from __future__ import annotations

from eth_abi import encode as abi_encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

NAME = "07_fees"


def _selector(sig: str) -> bytes:
    return function_signature_to_4byte_selector(sig)


def _build_recipient_fees(recipients, supplemental_bps):
    # FeeManager stores per-recipient fee in *fee units* (1e18 base?) — actual unit is
    # interpreted as basis points percentage scaled by split. We pass `split_bps`
    # weighting plus single supplemental_bps as `(addr, bps_supplemental_share)`.
    # Convention used here matches ipor-fusion RecipientFee struct: bps per recipient
    # such that sum equals supplemental_bps total.
    rows = []
    total = 0
    for r in recipients[:-1]:
        share = supplemental_bps * int(r["split_bps"]) // 10000
        rows.append((Web3.to_checksum_address(r["address"]), share))
        total += share
    # last absorbs rounding
    last = recipients[-1]
    rows.append((Web3.to_checksum_address(last["address"]), supplemental_bps - total))
    return rows


def run(cfg, deploy_ctx, session, instance, state, broadcast):
    if state.has_step(NAME):
        print(f"[{NAME}] already done — skipping")
        return
    fees = cfg.raw["fees"]
    fee_manager = instance["fee_manager"]
    mgmt = int(fees.get("management_supplemental_bps", 0))
    perf = int(fees.get("performance_supplemental_bps", 0))

    tx_hashes = []
    if mgmt > 0:
        rows = _build_recipient_fees(fees["recipients"], mgmt)
        print(f"[{NAME}] updateManagementFee supplemental={mgmt}bps recipients={rows}")
        calldata = _selector("updateManagementFee((address,uint256)[])") + abi_encode(
            ["(address,uint256)[]"], [rows]
        )
        rec = session.recorder.add(
            NAME, action="updateManagementFee", key="management",
            target=fee_manager, function="updateManagementFee((address,uint256)[])",
            args={"supplemental_bps": mgmt, "recipients": [[str(a), b] for a, b in rows]},
            calldata="0x" + calldata.hex(),
        )
        if broadcast:
            receipt = session.ctx.send(fee_manager, calldata)
            tx_hash = receipt["transactionHash"].hex()
            tx_hashes.append(tx_hash)
            rec.executed = True
            rec.tx_hash = tx_hash
            rec.gas_used = int(receipt["gasUsed"])
            print(f"[{NAME}]   mgmt tx {tx_hash} gas={receipt['gasUsed']}")

    if perf > 0:
        rows = _build_recipient_fees(fees["recipients"], perf)
        print(f"[{NAME}] updatePerformanceFee supplemental={perf}bps recipients={rows}")
        calldata = _selector("updatePerformanceFee((address,uint256)[])") + abi_encode(
            ["(address,uint256)[]"], [rows]
        )
        rec = session.recorder.add(
            NAME, action="updatePerformanceFee", key="performance",
            target=fee_manager, function="updatePerformanceFee((address,uint256)[])",
            args={"supplemental_bps": perf, "recipients": [[str(a), b] for a, b in rows]},
            calldata="0x" + calldata.hex(),
        )
        if broadcast:
            receipt = session.ctx.send(fee_manager, calldata)
            tx_hash = receipt["transactionHash"].hex()
            tx_hashes.append(tx_hash)
            rec.executed = True
            rec.tx_hash = tx_hash
            rec.gas_used = int(receipt["gasUsed"])
            print(f"[{NAME}]   perf tx {tx_hash} gas={receipt['gasUsed']}")

    if broadcast:
        state.record(NAME, tx_hashes=tx_hashes)
