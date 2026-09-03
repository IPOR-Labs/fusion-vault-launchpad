"""Helpers for AccessManager role grants that survive RPC backend lag.

Why this exists: the SDK's `AccessManager.has_role(...).call()` was observed to
return `is_member=False` even when the on-chain `hasRole(uint64,address)` view
returned True (Apex cbBTC, Base, 2026-05-26). Separately, on load-balanced RPCs
(e.g. Alchemy), `wait_for_transaction_receipt` does not guarantee the next call
sees the just-mined state — a grant for role X followed immediately by a grant
for role Y (whose admin is X) can revert in eth_estimateGas because the backend
serving estimateGas hasn't yet seen X being granted. Anvil instamine never
reproduces this; mainnet broadcasts do.

These helpers read `hasRole` directly from the contract via web3 and poll until
a freshly-granted role becomes visible before the caller moves on.
"""
from __future__ import annotations

import time

from web3 import Web3
from web3.types import ChecksumAddress

_HAS_ROLE_SELECTOR = Web3.keccak(text="hasRole(uint64,address)")[:4]


def has_role_raw(web3: Web3, am_address: ChecksumAddress, role_id: int, account: ChecksumAddress) -> bool:
    """Direct eth_call against AccessManager.hasRole — bypasses SDK wrapper."""
    data = (
        _HAS_ROLE_SELECTOR
        + role_id.to_bytes(32, "big")
        + b"\x00" * 12
        + bytes.fromhex(account[2:])
    )
    raw = web3.eth.call({"to": am_address, "data": data})
    return int.from_bytes(raw[:32], "big") != 0


def wait_for_member(
    web3: Web3,
    am_address: ChecksumAddress,
    role_id: int,
    account: ChecksumAddress,
    timeout_s: int = 30,
    poll_interval_s: float = 2.0,
) -> bool:
    """Poll hasRole until True or timeout. Returns True iff visible."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if has_role_raw(web3, am_address, role_id, account):
            return True
        time.sleep(poll_interval_s)
    return has_role_raw(web3, am_address, role_id, account)
