#!/usr/bin/env python3
"""Compare a dry-run plan artifact against a broadcast run artifact.

Turns the README's "read the dry-run output end-to-end" manual gate into a
deterministic check: a broadcast must not perform any on-chain action that the
deterministic plan did not foresee.

Usage:
  python tools/plan_diff.py .deploy-state/<name>.plan.json .deploy-state/<name>.run.json

Exit codes:
  0 — every broadcast action matched a planned action (plan_only entries, if any,
      are reported as benign: the broadcast skipped already-satisfied actions).
  1 — at least one run_only action (broadcast did something unplanned) — HARD FAIL.
  2 — usage / IO error.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from deploy.plan import diff_plan_run


def _load(path: str) -> dict:
    with Path(path).open() as f:
        return json.load(f)


def _fmt(a: dict) -> str:
    return f"{a['step']} :: {a['action']} :: key={a.get('key','')}"


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        print(__doc__)
        return 2
    try:
        plan, run = _load(argv[0]), _load(argv[1])
    except (OSError, json.JSONDecodeError) as e:
        print(f"error reading artifacts: {e}")
        return 2

    # Surface manifest divergence (e.g. plan vs run config_hash) — a different
    # config between plan and broadcast invalidates the comparison.
    pm, rm = plan.get("manifest", {}), run.get("manifest", {})
    if pm.get("config_hash") != rm.get("config_hash"):
        print(f"⚠️  config_hash differs: plan={pm.get('config_hash')} run={rm.get('config_hash')} "
              "— plan and run are for different configs; diff may be meaningless.")

    d = diff_plan_run(plan, run)
    print(f"matched={len(d['matched'])}  run_only={len(d['run_only'])}  plan_only={len(d['plan_only'])}")

    if d["plan_only"]:
        print("\nplan_only (planned but not broadcast — usually benign, e.g. already satisfied):")
        for a in d["plan_only"]:
            print(f"  · {_fmt(a)}")

    if d["run_only"]:
        print("\n❌ run_only (BROADCAST an action the plan never declared — HARD FAIL):")
        for a in d["run_only"]:
            print(f"  ✗ {_fmt(a)}  tx={a.get('tx_hash')}")
        return 1

    print("\n✅ no unplanned broadcast actions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
