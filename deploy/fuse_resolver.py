"""Resolve fuse names to addresses from the on-chain FuseWhitelist.

The whitelist is the authority for which fuse address is current for a given
(fuse type, market). The deploy-context `fuses{}` map is only a hand-maintained
cache and goes stale whenever IPOR ships a new fuse version (observed on
Arbitrum 2026-09-25: ipor-abi and the context listed a `deprecated` ERC4626
supply fuse while the whitelist carried the `active` successor). So before any
step runs, every fuse the strategy names is resolved here:

  1. name -> whitelist fuse type: the name is a whitelist type name
     (`EulerV2SupplyFuse`, `ERC20BalanceFuse`, ...), or a context key whose
     address the whitelist knows (its type is read from `getFuseByAddress`).
  2. type + market -> `getFusesByTypeAndMarketIdAndStatus(type, market, active)`.
     The market is `balance_fuses[].market` for balance fuses, `fuses[].market`
     when given, otherwise inferred: the type must resolve in exactly one of
     the markets the strategy declares balance fuses for.
  3. exactly one active address -> use it (and warn when the context disagrees);
     several -> the context address if it is one of them, else an error naming
     them; none -> the context address with a warning (the whitelist gate in
     `deploy/whitelist.py` still decides whether it may be added).

Pure logic (`resolve_refs`) is SDK-free and unit-tested; `WhitelistIndex` does
the eth_calls. `apply_whitelist_resolution` wires both into the DeployContext
as overrides, so every step keeps calling `deploy_ctx.fuse(name)` unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from eth_abi import decode as abi_decode, encode as abi_encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from deploy.whitelist import STATE_ACTIVE, STATE_NAMES, ZERO, _call, read_type_names


@dataclass(frozen=True, slots=True)
class FuseRef:
    name: str
    market: str | None = None   # market name; None = infer from the declared balance-fuse markets
    role: str = "fuse"          # "fuse" | "balance" | "queue" (for messages only)


@dataclass
class Resolution:
    overrides: dict[str, str] = field(default_factory=dict)   # name -> checksum address
    notes: list[str] = field(default_factory=list)            # informational lines
    warns: list[str] = field(default_factory=list)
    fails: list[str] = field(default_factory=list)


class WhitelistIndex:
    """Thin, memoised reader over FuseWhitelist for the lookups the resolver needs."""

    def __init__(self, w3, whitelist: str):
        self.w3, self.whitelist = w3, Web3.to_checksum_address(whitelist)
        self._types: dict[str, int] | None = None
        self._by_addr: dict[str, tuple[int, int]] = {}
        self._hits: dict[tuple[int, int], list[str]] = {}

    def type_id(self, type_name: str) -> int | None:
        if self._types is None:
            self._types = {n: i for i, n in read_type_names(self.w3, self.whitelist).items()}
        return self._types.get(type_name)

    def type_of_address(self, addr: str) -> tuple[int, int] | None:
        """(state, type) of a listed address, None when the whitelist does not know it."""
        key = addr.lower()
        if key not in self._by_addr:
            sel = function_signature_to_4byte_selector("getFuseByAddress(address)")
            ret = _call(self.w3, self.whitelist, sel + abi_encode(["address"], [Web3.to_checksum_address(addr)]))
            state, ftype, faddr, _ts = abi_decode(["uint16", "uint16", "address", "uint32"], bytes(ret))
            self._by_addr[key] = (int(state), int(ftype)) if faddr.lower() != ZERO else None
        return self._by_addr[key]

    def active(self, type_id: int, market_id: int) -> list[str]:
        key = (type_id, market_id)
        if key not in self._hits:
            sel = function_signature_to_4byte_selector("getFusesByTypeAndMarketIdAndStatus(uint16,uint256,uint16)")
            ret = _call(self.w3, self.whitelist, sel + abi_encode(["uint16", "uint256", "uint16"], [type_id, market_id, STATE_ACTIVE]))
            self._hits[key] = [Web3.to_checksum_address(a) for a in abi_decode(["address[]"], ret)[0]]
        return self._hits[key]


def config_refs(cfg_raw: dict) -> list[FuseRef]:
    """Every fuse name the strategy hands to the vault, with the market when the config states it."""
    refs: dict[str, FuseRef] = {}
    for bf in cfg_raw.get("balance_fuses", []):
        refs.setdefault(bf["fuse"], FuseRef(bf["fuse"], bf["market"], "balance"))
    for f in cfg_raw.get("fuses", []):
        refs.setdefault(f["name"], FuseRef(f["name"], f.get("market"), "fuse"))
    for e in cfg_raw.get("instant_withdrawal", {}).get("order", []):
        refs.setdefault(e["fuse"], FuseRef(e["fuse"], None, "queue"))
    return list(refs.values())


def resolve_refs(refs: list[FuseRef], index, context_fuses: dict[str, str], market_id_of,
                 declared_markets: list[str]) -> Resolution:
    """Pure resolution over an index with `type_id(name)`, `type_of_address(addr)`, `active(type, market)`.

    `market_id_of(name) -> int` maps market names; `declared_markets` are the balance-fuse markets
    used for inference. Returns overrides for every name that resolved on the whitelist."""
    res = Resolution()
    for ref in refs:
        ctx_addr = context_fuses.get(ref.name)
        ctx_cs = Web3.to_checksum_address(ctx_addr) if ctx_addr else None
        # 1. name -> type
        tid = index.type_id(ref.name)
        via = "type name"
        if tid is None and ctx_cs:
            known = index.type_of_address(ctx_cs)
            if known:
                tid = known[1]
                via = f"type of context address {ctx_cs}"
        if tid is None:
            if ctx_cs:
                res.warns.append(f"{ref.name}: not a whitelist type name and the context address {ctx_cs} is not listed — using the context address; the whitelist gate decides. Prefer naming the fuse by its whitelist type (getFuseTypes) plus \"market\"")
                continue
            res.fails.append(f"{ref.name}: unknown — neither a FuseWhitelist type name nor a key in the context 'fuses' map")
            continue
        # 2. type + market -> active addresses
        if ref.market:
            candidates = {ref.market: index.active(tid, market_id_of(ref.market))}
        else:
            candidates = {m: index.active(tid, market_id_of(m)) for m in declared_markets}
            candidates = {m: a for m, a in candidates.items() if a}
            if len(candidates) > 1:
                res.fails.append(f"{ref.name}: active on several declared markets ({', '.join(sorted(candidates))}) — add \"market\" to the fuses[] entry")
                continue
        hits = next(iter(candidates.values()), []) if candidates else []
        market = next(iter(candidates)) if candidates else (ref.market or "?")
        # 3. pick
        if len(hits) == 1:
            chosen = hits[0]
        elif len(hits) > 1:
            if ctx_cs and ctx_cs.lower() in {h.lower() for h in hits}:
                chosen = ctx_cs
                res.notes.append(f"{ref.name}: {len(hits)} active on {market}; keeping the context's {ctx_cs}")
            else:
                res.fails.append(f"{ref.name}: {len(hits)} active fuses of this type on {market} ({', '.join(hits)}) — pin one in the context 'fuses' map")
                continue
        else:
            if ctx_cs:
                res.warns.append(f"{ref.name}: no active fuse of this type on {market} — using the context address {ctx_cs}; the whitelist gate decides")
            else:
                res.fails.append(f"{ref.name}: no active fuse of this type on {market} and no context address to fall back on")
            continue
        res.overrides[ref.name] = chosen
        if ctx_cs and ctx_cs.lower() != chosen.lower():
            res.warns.append(f"{ref.name}: context has {ctx_cs} but the whitelist's active fuse on {market} is {chosen} — using the whitelist (update the context)")
        res.notes.append(f"{ref.name} [{via}] market={market} -> {chosen}")
    return res


def apply_whitelist_resolution(cfg_raw: dict, deploy_ctx, w3, log=print) -> Resolution:
    """Resolve every fuse the strategy names and install the results as context overrides."""
    index = WhitelistIndex(w3, deploy_ctx.fuse_whitelist)
    declared = sorted({bf["market"] for bf in cfg_raw.get("balance_fuses", [])})
    res = resolve_refs(config_refs(cfg_raw), index, deploy_ctx.raw.get("fuses", {}), deploy_ctx.market_id, declared)
    log(f"[fuse_resolver] FuseWhitelist @ {index.whitelist}: {len(res.overrides)} of {len(config_refs(cfg_raw))} fuse names resolved on-chain")
    for n in res.notes:
        log(f"    resolve  {n}")
    for w in res.warns:
        log(f"    ⚠️  {w}")
    if res.fails:
        raise RuntimeError("fuse resolution failed: " + "; ".join(res.fails))
    for name, addr in res.overrides.items():
        deploy_ctx.set_fuse_override(name, addr)
    return res
