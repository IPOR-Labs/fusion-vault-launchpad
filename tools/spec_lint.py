#!/usr/bin/env python3
"""Deterministic reconciliation of a human spec (<name>.md) against the machine
spec (<name>.json) that actually drives deploy.py.

The markdown is the reviewer's source of truth; the JSON is what deploys. They
can silently drift. This lint enforces the contract a reviewer cares about:

  ERROR (exit 1):
    * the JSON vault name / symbol must appear verbatim in the markdown
      (unambiguous identity drift between the two specs).
  WARN (exit 0):
    * the JSON underlying token address is not acknowledged in the markdown in
      any address form (specs often refer to the token by symbol only, which is
      fine — but the reviewer then can't verify the concrete address).
    * any address written in the markdown that does not resolve to a hex value
      present in the JSON or the chain context — i.e. the spec references
      something the deploy never touches.

Markdown addresses are matched ellipsis-aware: a token like ``0xA216…b40A``
matches any concrete address that starts with ``0xA216`` and ends with ``b40A``.

Usage:
  python tools/spec_lint.py strategies/<name>.json [--md strategies/<name>.md]

Exit codes: 0 ok (warnings allowed) · 1 reconciliation error · 2 usage/IO error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# A 20-byte address, or an ellipsized form: 0x<hex>…<hex> / 0x<hex>...<hex>.
_FULL_ADDR = re.compile(r"0x[0-9a-fA-F]{40}")
_ELLIPSIS_ADDR = re.compile(r"0x[0-9a-fA-F]{2,}\s*(?:…|\.\.\.|\.\.)\s*[0-9a-fA-F]{2,}")
# Any hex blob the deploy touches — addresses (40) AND bytes32 substrate values
# (64), e.g. Morpho market ids — so an ellipsized md token can resolve to either.
_HEX_BLOB = re.compile(r"0x[0-9a-fA-F]{40,64}")


def _collect_json_addresses(obj) -> set[str]:
    """Every full 0x… hex blob (address or bytes32) in a nested structure (lower)."""
    found: set[str] = set()
    if isinstance(obj, str):
        for m in _HEX_BLOB.findall(obj):
            found.add(m.lower())
    elif isinstance(obj, dict):
        for v in obj.values():
            found |= _collect_json_addresses(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= _collect_json_addresses(v)
    return found


def _md_address_tokens(md: str) -> list[str]:
    """Address-like tokens from the markdown (full + ellipsized), original form."""
    tokens = list(_FULL_ADDR.findall(md))
    tokens += [m.group(0) for m in _ELLIPSIS_ADDR.finditer(md)]
    return tokens


def _token_matches(token: str, addresses: set[str]) -> bool:
    """Does an md token (full or ellipsized) resolve to any known address?"""
    t = token.lower().replace(" ", "")
    if _FULL_ADDR.fullmatch(t):
        return t in addresses
    # ellipsized: split on the ellipsis, take hex prefix/suffix
    parts = re.split(r"…|\.\.\.|\.\.", t)
    if len(parts) != 2:
        return False
    prefix, suffix = parts[0], parts[1]
    return any(a.startswith(prefix) and a.endswith(suffix) for a in addresses)


def lint(json_path: Path, md_path: Path) -> int:
    cfg = json.loads(json_path.read_text())
    md = md_path.read_text()

    errors: list[str] = []
    warns: list[str] = []

    vault = cfg.get("vault", {})
    name = vault.get("name", "")
    symbol = vault.get("symbol", "")
    underlying = (vault.get("underlying") or "").lower()

    if name and name not in md:
        errors.append(f"vault.name {name!r} (JSON) not found in markdown")
    if symbol and symbol not in md:
        errors.append(f"vault.symbol {symbol!r} (JSON) not found in markdown")

    md_tokens = _md_address_tokens(md)
    # WARN: the underlying address ideally appears in the spec (symbol-only is ok).
    if underlying and not any(_token_matches(t, {underlying}) for t in md_tokens):
        warns.append(f"underlying {underlying} (JSON) not referenced by address in markdown "
                     "(spec refers to it by symbol only — reviewer can't verify the address)")

    # WARN: md addresses that don't resolve to anything the deploy touches.
    known = _collect_json_addresses(cfg)
    # include chain-context addresses (fuses/factories/middleware) as legitimate
    try:
        from deploy.context import load_context
        ctx = load_context(cfg["chain"]["context"])
        known |= _collect_json_addresses(ctx.raw)
    except Exception as e:  # context is a best-effort enrichment for the WARN set
        warns.append(f"could not load chain context for cross-check: {e!r}")

    for tok in dict.fromkeys(md_tokens):  # dedupe, preserve order
        if not _token_matches(tok, known):
            warns.append(f"markdown address {tok} does not resolve to any JSON or context address")

    print(f"spec-lint {json_path.name} ↔ {md_path.name}: "
          f"{len(errors)} error(s), {len(warns)} warning(s)")
    for w in warns:
        print(f"  ⚠️  {w}")
    for e in errors:
        print(f"  ❌ {e}")
    if not errors and not warns:
        print("  ✅ specs reconciled")
    return 1 if errors else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="md ↔ json spec reconciliation")
    p.add_argument("json", help="path to strategy JSON")
    p.add_argument("--md", default=None, help="path to markdown spec (default: same stem .md)")
    args = p.parse_args(argv)

    json_path = Path(args.json)
    md_path = Path(args.md) if args.md else json_path.with_suffix(".md")
    if not json_path.exists():
        print(f"error: {json_path} not found")
        return 2
    if not md_path.exists():
        print(f"error: markdown spec {md_path} not found")
        return 2
    return lint(json_path, md_path)


if __name__ == "__main__":
    raise SystemExit(main())
