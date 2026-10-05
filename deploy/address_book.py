"""Cross-check every non-fuse address a deploy context resolves against the chain.

Fuses are resolved and cross-checked by `deploy/fuse_resolver.py` (FuseWhitelist first,
ipor-abi second). Everything else comes from the vendored ipor-abi snapshot, a pin or an
external address, and is checked here before any step runs:

  chain id          the RPC's chain id equals the context's                     FAIL
  roots             factory, oracle middleware, FuseWhitelist have code          FAIL
                    factory.getPriceOracleMiddleware() equals ipor-abi's         FAIL
  standard fuses    have code; the factory's burn-request-fee fuse is listed     WARN
                    newest one not active on the whitelist                       WARN
  pre-hooks,        have code                                                    FAIL
  callback          a listed pre-hook that is not active on the whitelist        WARN
  handlers,
  feed factories
  externals         have code; the Aave pool equals provider.getPool()           FAIL
  tokens            have code                                                    FAIL
                    symbol() matches the context's symbol (case-insensitive)     WARN
  snapshot          older than SNAPSHOT_MAX_AGE_DAYS                              WARN

`address_rows` is pure over a reader (unit-tested with a fake); `ChainReader` does the
eth_calls. `check_context_addresses` prints the table and raises on any FAIL.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from eth_abi import decode as abi_decode, encode as abi_encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from deploy.whitelist import STATE_ACTIVE, STATE_NAMES, ZERO, read_type_names

OK, WARN, FAIL = "ok", "warn", "fail"
SNAPSHOT_MAX_AGE_DAYS = 45


@dataclass(frozen=True, slots=True)
class AddrRow:
    category: str
    name: str
    address: str
    source: str      # "ipor-abi <key>", "pin", "external", "token"
    verdict: str     # ok | warn | fail
    note: str = ""


class ChainReader:
    """eth_call readers the checks need; every method returns None when the call reverts."""

    def __init__(self, w3):
        self.w3 = w3
        self._types: dict[int, str] | None = None

    def chain_id(self) -> int:
        return int(self.w3.eth.chain_id)

    def has_code(self, addr: str) -> bool:
        return len(bytes(self.w3.eth.get_code(Web3.to_checksum_address(addr)))) > 0

    def _call(self, to: str, sig: str, out: list[str], types: list[str] | None = None, args: list | None = None):
        data = function_signature_to_4byte_selector(sig) + (abi_encode(types, args) if types else b"")
        try:
            ret = bytes(self.w3.eth.call({"to": Web3.to_checksum_address(to), "data": data}))
            return abi_decode(out, ret)
        except Exception:
            return None

    def address_getter(self, contract: str, sig: str):
        r = self._call(contract, sig, ["address"])
        return Web3.to_checksum_address(r[0]) if r else None

    def symbol(self, token: str):
        r = self._call(token, "symbol()", ["string"])
        return r[0] if r else None

    def whitelist_entry(self, whitelist: str, addr: str):
        """(state_name, type_name) of a listed address, None when unlisted or unreadable."""
        r = self._call(whitelist, "getFuseByAddress(address)", ["uint16", "uint16", "address", "uint32"],
                       ["address"], [Web3.to_checksum_address(addr)])
        if not r or r[2].lower() == ZERO:
            return None
        if self._types is None:
            try:
                self._types = read_type_names(self.w3, whitelist)
            except Exception:
                self._types = {}
        return STATE_NAMES.get(int(r[0]), f"state#{r[0]}"), self._types.get(int(r[1]), f"type#{r[1]}")


def _norm_symbol(sym: str) -> str:
    """Compare symbols case-insensitively and with the tether sign (USD₮0) spelled as T."""
    return sym.replace("₮", "T").casefold()


def _age_days(fetched: str, today: dt.date) -> int | None:
    try:
        return (today - dt.date.fromisoformat(fetched)).days
    except (TypeError, ValueError):
        return None


def address_rows(ctx, reader, today: dt.date | None = None) -> list[AddrRow]:
    """Pure over `reader` (see ChainReader): one row per checked address."""
    today = today or dt.date.today()
    rows: list[AddrRow] = []
    snap = ctx.snapshot
    add = rows.append

    # snapshot provenance
    age = _age_days(snap.source.get("fetched", ""), today)
    note = f"ipor-abi {snap.commit[:10] or '?'} fetched {snap.source.get('fetched', '?')}"
    add(AddrRow("snapshot", snap.deployment, "", "ipor-abi", WARN if age is None or age > SNAPSHOT_MAX_AGE_DAYS else OK,
                note + ("" if age is not None and age <= SNAPSHOT_MAX_AGE_DAYS else " — run `make ipor-abi`")))

    # chain id
    cid = reader.chain_id()
    add(AddrRow("chain", "chain_id", "", "rpc", OK if cid == ctx.chain_id else FAIL,
                f"rpc {cid}, context {ctx.chain_id}"))

    def resolved(category, name, fn, source):
        try:
            return fn()
        except KeyError as e:
            add(AddrRow(category, name, "", source, FAIL, str(e).strip("'\"")))
            return None

    def code_row(category, name, addr, source, extra=""):
        has = reader.has_code(addr)
        add(AddrRow(category, name, addr, source, OK if has else FAIL, extra if has else "no contract code at this address"))
        return has

    contracts = ctx.raw.get("contracts", {})
    roots = {}
    for ref, key in contracts.items():
        addr = resolved("contract", ref, lambda r=ref: ctx.resolve_address(r), f"ipor-abi {key}")
        if not addr:
            continue
        roots[ref] = addr
        if ref == "middleware_owner":   # an account (multisig or EOA), not necessarily a contract
            add(AddrRow("contract", ref, addr, f"ipor-abi {key}", OK, "fork impersonation only"))
        else:
            code_row("contract", ref, addr, f"ipor-abi {key}")

    factory, pom, wl = roots.get("fusion_factory"), roots.get("price_oracle_middleware"), roots.get("fuse_whitelist")
    if factory and pom:
        got = reader.address_getter(factory, "getPriceOracleMiddleware()")
        if got is None:
            add(AddrRow("contract", "factory.getPriceOracleMiddleware()", factory, "chain", WARN, "getter not available on this factory version"))
        else:
            add(AddrRow("contract", "factory.getPriceOracleMiddleware()", got, "chain",
                        OK if got.lower() == pom.lower() else FAIL,
                        "matches ipor-abi" if got.lower() == pom.lower() else f"ipor-abi says {pom}"))

    # standard fuses
    std = resolved("standard_fuse", "standard_fuses", ctx.standard_fuses, "ipor-abi") or []
    pairs = list(zip(ctx.raw.get("standard_fuses", []), std))
    for i, (key, addr) in enumerate(pairs):
        # older versions stay on every vault cloned before the upgrade, so only the newest must be active
        if code_row("standard_fuse", key, addr, f"ipor-abi {key}") and wl and i == len(pairs) - 1:
            entry = reader.whitelist_entry(wl, addr)
            if entry and entry[0] != STATE_NAMES[STATE_ACTIVE]:
                add(AddrRow("standard_fuse", key, addr, "whitelist", WARN, f"whitelist state {entry[0]} (type {entry[1]})"))
    if factory and std:
        burn = reader.address_getter(factory, "getBurnRequestFeeFuseAddress()")
        if burn and burn.lower() not in {a.lower() for a in std}:
            add(AddrRow("standard_fuse", "factory.getBurnRequestFeeFuseAddress()", burn, "chain", WARN,
                        "the factory injects a fuse the context does not list as standard"))

    # pre-hooks, callback handlers, price-feed factories
    for name, key in ctx.raw.get("pre_hooks", {}).items():
        addr = resolved("pre_hook", name, lambda n=name: ctx.pre_hook(n), f"ipor-abi {key}")
        if addr and code_row("pre_hook", name, addr, "pin" if ctx.pin(name) else f"ipor-abi {key}") and wl:
            entry = reader.whitelist_entry(wl, addr)
            if entry and entry[0] != STATE_NAMES[STATE_ACTIVE]:
                add(AddrRow("pre_hook", name, addr, "whitelist", WARN, f"whitelist state {entry[0]} (type {entry[1]})"))
    for name, key in ctx.raw.get("callback_handlers", {}).items():
        addr = resolved("callback_handler", name, lambda n=name: ctx.callback_handler(n), f"ipor-abi {key}")
        if addr:
            code_row("callback_handler", name, addr, f"ipor-abi {key}")
    for name, key in ctx.raw.get("price_feed_factories", {}).items():
        addr = resolved("price_feed_factory", name, lambda n=name: ctx.price_feed_factory(n), f"ipor-abi {key}")
        if addr:
            code_row("price_feed_factory", name, addr, f"ipor-abi {key}")

    # pins (fuse pins are re-validated by the fuse resolver; listed here for the record)
    for name, p in ctx.raw.get("pins", {}).items():
        add(AddrRow("pin", name, Web3.to_checksum_address(p["address"]), "pin", OK, p["reason"]))

    # third-party addresses
    for name, e in ctx.raw.get("external_addresses", {}).items():
        addr = Web3.to_checksum_address(e["address"])
        if code_row("external", name, addr, "external", e.get("source", "")) and name == "aave_v3_pool":
            provider = snap.get("AaveV3PoolAddressesProvider")
            pool = reader.address_getter(provider, "getPool()") if provider else None
            if pool is not None:
                add(AddrRow("external", "AaveV3PoolAddressesProvider.getPool()", pool, "chain",
                            OK if pool.lower() == addr.lower() else FAIL,
                            "matches" if pool.lower() == addr.lower() else f"context has {addr}"))
    for sym, a in ctx.raw.get("tokens", {}).items():
        addr = Web3.to_checksum_address(a)
        if not reader.has_code(addr):
            add(AddrRow("token", sym, addr, "token", FAIL, "no contract code at this address"))
            continue
        got = reader.symbol(addr)
        same = got is not None and _norm_symbol(got) == _norm_symbol(sym)
        add(AddrRow("token", sym, addr, "token", OK if same else WARN,
                    "" if got == sym else f"symbol() returns {got!r}"))
    return rows


def format_rows(rows: list[AddrRow], show_ok: bool = False) -> list[str]:
    icon = {OK: "✅", WARN: "⚠️ ", FAIL: "❌"}
    out = []
    for r in rows:
        if r.verdict == OK and not show_ok and r.category not in ("snapshot", "pin"):
            continue
        out.append(f"    {icon[r.verdict]} {r.category:18s} {r.name:40s} {r.address or '-':42s} {r.source}"
                   + (f" — {r.note}" if r.note else ""))
    return out


def check_context_addresses(deploy_ctx, w3, log=print, show_ok: bool = False) -> list[AddrRow]:
    rows = address_rows(deploy_ctx, ChainReader(w3))
    fails = [r for r in rows if r.verdict == FAIL]
    warns = [r for r in rows if r.verdict == WARN]
    log(f"[address_book] context {deploy_ctx.name}: {len(rows)} checks, {len(warns)} warning(s), {len(fails)} failure(s)")
    for line in format_rows(rows, show_ok):
        log(line)
    if fails:
        raise RuntimeError("context address check failed: " + "; ".join(f"{r.category} {r.name}: {r.note}" for r in fails))
    return rows
