"""On-chain FuseWhitelist gate — the authority for "is this fuse address real, current
and safe to add" (the deploy-context JSON is only a hand-maintained cache of it).

Pure classification lives in `whitelist_problems` (SDK-free, unit-tested); the two
thin readers at the bottom take a `w3` (web3.Web3) and do raw eth_calls so they work
from any step without the SDK wrappers.

Whitelist states (getFuseStates(), Ethereum + Base 2026-09-03):
  0 default · 1 active · 2 deprecated · 3 removed
Policy: unlisted / removed / deprecated -> hard FAIL (deprecated means IPOR replaced it —
use the active successor; e.g. the market-12 UniversalTokenSwapperFuse is `removed`,
its successor is UniversalTokenSwapperFuseV2 on market 1202). `default` -> WARN only
(fuse added without a state — confirm with the whitelist manager).
"""
from __future__ import annotations

from dataclasses import dataclass

from eth_abi import decode as abi_decode, encode as abi_encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

STATE_DEFAULT, STATE_ACTIVE, STATE_DEPRECATED, STATE_REMOVED = 0, 1, 2, 3
STATE_NAMES = {0: "default", 1: "active", 2: "deprecated", 3: "removed"}
ZERO = "0x0000000000000000000000000000000000000000"


@dataclass(frozen=True, slots=True)
class FuseStatus:
    name: str          # how the config refers to it (context key)
    address: str
    listed: bool
    state: int
    type_name: str     # whitelist fuse-type description ("" when unlisted)

    @property
    def state_name(self) -> str:
        return STATE_NAMES.get(self.state, f"state#{self.state}")


def whitelist_problems(statuses: list[FuseStatus]) -> tuple[list[str], list[str]]:
    """Pure: ``(fails, warns)`` for a set of fuse statuses. Empty fails = safe to add."""
    fails, warns = [], []
    for s in statuses:
        tag = f"{s.name} {s.address}"
        if not s.listed:
            fails.append(f"{tag}: NOT on the FuseWhitelist — wrong address or never whitelisted")
        elif s.state == STATE_REMOVED:
            fails.append(f"{tag}: whitelist state=removed (type {s.type_name}) — use the active successor")
        elif s.state == STATE_DEPRECATED:
            fails.append(f"{tag}: whitelist state=deprecated (type {s.type_name}) — use the active successor")
        elif s.state == STATE_DEFAULT:
            warns.append(f"{tag}: whitelist state=default (type {s.type_name}) — no state set, confirm with the whitelist manager")
        elif s.state != STATE_ACTIVE:
            warns.append(f"{tag}: unknown whitelist state {s.state} (type {s.type_name})")
    return fails, warns


def decode_fuse_by_address(name: str, address: str, ret: bytes, type_names: dict[int, str]) -> FuseStatus:
    """Decode a raw ``getFuseByAddress(address)`` return: (uint16 state, uint16 type, address, uint32 ts)."""
    state, fuse_type, fuse_addr, _ts = abi_decode(["uint16", "uint16", "address", "uint32"], bytes(ret))
    listed = fuse_addr.lower() != ZERO
    return FuseStatus(name=name, address=Web3.to_checksum_address(address), listed=listed,
                      state=int(state), type_name=type_names.get(int(fuse_type), "") if listed else "")


def config_fuse_refs(cfg_raw: dict, deploy_ctx) -> list[tuple[str, str]]:
    """Every (name, address) the deployer will hand to the vault: functional fuses,
    balance fuses and instant-withdraw queue fuses. Pre-hooks are NOT on the whitelist
    (only ExchangeRateValidatorPreHook is listed, 2026-09-03) so they are not checked."""
    refs: dict[str, str] = {}
    for f in cfg_raw.get("fuses", []):
        refs.setdefault(f["name"], deploy_ctx.fuse(f["name"]))
    for bf in cfg_raw.get("balance_fuses", []):
        refs.setdefault(bf["fuse"], deploy_ctx.fuse(bf["fuse"]))
    for e in cfg_raw.get("instant_withdrawal", {}).get("order", []):
        refs.setdefault(e["fuse"], deploy_ctx.fuse(e["fuse"]))
    return list(refs.items())


# --- chain readers -------------------------------------------------------------------

def _call(w3, to: str, data: bytes) -> bytes:
    return bytes(w3.eth.call({"to": Web3.to_checksum_address(to), "data": data}))


def read_type_names(w3, whitelist: str) -> dict[int, str]:
    ret = _call(w3, whitelist, function_signature_to_4byte_selector("getFuseTypes()"))
    ids, names = abi_decode(["uint16[]", "string[]"], ret)
    return dict(zip((int(i) for i in ids), names))


def read_fuse_statuses(w3, whitelist: str, refs: list[tuple[str, str]]) -> list[FuseStatus]:
    type_names = read_type_names(w3, whitelist)
    sel = function_signature_to_4byte_selector("getFuseByAddress(address)")
    out = []
    for name, addr in refs:
        ret = _call(w3, whitelist, sel + abi_encode(["address"], [Web3.to_checksum_address(addr)]))
        out.append(decode_fuse_by_address(name, addr, ret, type_names))
    return out


def read_fuses_by_market(w3, whitelist: str, market_id: int) -> list[str]:
    sel = function_signature_to_4byte_selector("getFusesByMarketId(uint256)")
    ret = _call(w3, whitelist, sel + abi_encode(["uint256"], [int(market_id)]))
    return [Web3.to_checksum_address(a) for a in abi_decode(["address[]"], ret)[0]]


def assert_whitelisted(cfg_raw: dict, deploy_ctx, w3, log=print) -> list[FuseStatus]:
    """Gate used by the deployer before ``addFuses``: raise on any fail, print warns."""
    whitelist = deploy_ctx.fuse_whitelist
    statuses = read_fuse_statuses(w3, whitelist, config_fuse_refs(cfg_raw, deploy_ctx))
    fails, warns = whitelist_problems(statuses)
    for s in statuses:
        log(f"    whitelist {s.state_name:10s} {s.name} -> {s.address} ({s.type_name or 'unlisted'})")
    for w in warns:
        log(f"    ⚠️  {w}")
    if fails:
        raise RuntimeError("FuseWhitelist gate failed (" + f"{whitelist}): " + "; ".join(fails))
    return statuses
