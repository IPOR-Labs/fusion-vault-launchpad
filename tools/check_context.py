#!/usr/bin/env python3
"""Check every address a chain context resolves against the chain (`make check-context`).

    python tools/check_context.py base-fusion          # RPC_URL, else the context's public_rpc
    python tools/check_context.py --all                # every context with a public_rpc

Prints one row per address: where it comes from (ipor-abi key, pin, external, token) and
whether the chain agrees. Exit 1 on any failure. Fuses are not listed here: they are
resolved per strategy on the FuseWhitelist when a run starts.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from web3 import Web3  # noqa: E402

from deploy.address_book import FAIL, ChainReader, address_rows, format_rows  # noqa: E402
from deploy.context import CONTEXTS_DIR, load_context  # noqa: E402


def check(name: str, rpc: str | None) -> bool:
    ctx = load_context(name)
    url = rpc or ctx.public_rpc
    if not url:
        print(f"{name}: no RPC (set RPC_URL or add public_rpc to the context)")
        return False
    rows = address_rows(ctx, ChainReader(Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 30}))))
    fails = [r for r in rows if r.verdict == FAIL]
    print(f"{name}: {len(rows)} checks, {sum(r.verdict == 'warn' for r in rows)} warning(s), {len(fails)} failure(s)")
    for line in format_rows(rows, show_ok=True):
        print(line)
    return not fails


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("context", nargs="?", help="context name, e.g. base-fusion")
    ap.add_argument("--all", action="store_true", help="check every context with a public_rpc (ignores RPC_URL)")
    args = ap.parse_args(argv)
    if args.all:
        names = [p.stem for p in sorted(CONTEXTS_DIR.glob("*.json"))]
        results = [check(n, None) for n in names]
    elif args.context:
        results = [check(args.context, os.environ.get("RPC_URL"))]
    else:
        ap.error("name a context or pass --all")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
