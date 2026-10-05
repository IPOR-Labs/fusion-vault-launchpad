#!/usr/bin/env python3
"""Refresh the vendored ipor-abi snapshots in contexts/ipor-abi/ (`make ipor-abi`).

For every chain context, download `mainnet/<ipor_abi_deployment>/addresses.json` from
IPOR-Labs/ipor-abi at the current `main` commit (or `--ref`), write it with its commit and
fetch date, and print the keys that were added, removed or changed. Review the diff before
committing: a changed key moves every vault deployed afterwards to the new contract.

    python tools/refresh_ipor_abi.py            # all contexts, ipor-abi main
    python tools/refresh_ipor_abi.py --ref <sha> --check   # exit 1 if a snapshot is behind
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from deploy.ipor_abi import REPOSITORY, SNAPSHOT_DIR, snapshot_diff, snapshot_document  # noqa: E402

CONTEXTS = ROOT / "contexts"


def head_commit(ref: str) -> str:
    if len(ref) == 40:
        return ref
    out = subprocess.run(["git", "ls-remote", f"https://github.com/{REPOSITORY}", ref],
                         capture_output=True, text=True, check=True, timeout=60).stdout.split()
    if not out:
        raise SystemExit(f"ref '{ref}' not found in {REPOSITORY}")
    return out[0]


def fetch(deployment: str, commit: str) -> dict:
    url = f"https://raw.githubusercontent.com/{REPOSITORY}/{commit}/mainnet/{deployment}/addresses.json"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "fusion-vault-launchpad"}), timeout=60) as r:
        return json.loads(r.read())


def deployments() -> list[str]:
    out = []
    for p in sorted(CONTEXTS.glob("*.json")):
        d = json.loads(p.read_text()).get("ipor_abi_deployment")
        if d and d not in out:
            out.append(d)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", default="main", help="ipor-abi branch, tag or commit (default main)")
    ap.add_argument("--check", action="store_true", help="only report; exit 1 if any snapshot differs from --ref")
    args = ap.parse_args(argv)

    commit = head_commit(args.ref)
    today = dt.date.today().isoformat()
    behind = False
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for dep in deployments():
        path = SNAPSHOT_DIR / f"{dep}.json"
        old = json.loads(path.read_text()) if path.exists() else {"addresses": {}, "source": {}}
        new = fetch(dep, commit)
        added, removed, changed = snapshot_diff(old["addresses"], new)
        was = str(old.get("source", {}).get("commit", ""))[:10] or "none"
        print(f"{dep}: {was} -> {commit[:10]}  +{len(added)} -{len(removed)} ~{len(changed)}")
        for k in added:
            print(f"    + {k} {new[k]}")
        for k in removed:
            print(f"    - {k} {old['addresses'][k]}")
        for k in changed:
            print(f"    ~ {k} {old['addresses'][k]} -> {new[k]}")
        if added or removed or changed:
            behind = True
        if not args.check:
            path.write_text(json.dumps(snapshot_document(dep, new, commit, today), indent=2) + "\n")
    if args.check:
        return 1 if behind else 0
    print(f"snapshots written at ipor-abi {commit[:10]}; review the diff, then run `make test`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
