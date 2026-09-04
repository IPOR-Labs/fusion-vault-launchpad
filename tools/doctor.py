#!/usr/bin/env python3
"""Environment check for humans and agents. Prints what works and which modes are
available. Never prints secrets: it reports whether DEPLOYER_PRIVATE_KEY is set,
never its value.

Usage:
  python tools/doctor.py [strategies/<name>.json]

Exit code 0 when a dry-run is possible, 1 otherwise.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK, WARN, FAIL = "✅", "⚠️ ", "❌"


def main(argv: list[str]) -> int:
    strategy = Path(argv[0]) if argv else ROOT / "strategies" / "example-usdc-aave-base.json"
    rows: list[tuple[str, str]] = []
    dry_run_ok = True
    rehearsal_ok = True
    live_ok = True

    # Python
    v = sys.version_info
    rows.append((OK if v >= (3, 12) else FAIL, f"Python {v.major}.{v.minor}.{v.micro} (3.12+ required)"))
    dry_run_ok &= v >= (3, 12)

    # SDK and deps
    try:
        import importlib.metadata as m
        import ipor_fusion.core.fusion_factory  # noqa: F401
        import ipor_fusion.core.context  # noqa: F401
        rows.append((OK, f"ipor-fusion SDK {m.version('ipor-fusion')}, web3 {m.version('web3')}, jsonschema {m.version('jsonschema')}"))
    except Exception as e:  # pragma: no cover
        rows.append((FAIL, f"ipor-fusion SDK not importable ({e!r}); run: pip install -r requirements-dev.txt"))
        dry_run_ok = False

    # .env
    env_path = ROOT / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path)
        except ImportError:
            pass
        rows.append((OK, ".env present (values are not shown)"))
    else:
        rows.append((WARN, ".env absent: fine for a dry-run; copy .env.example when you need to broadcast"))

    rpc = os.environ.get("RPC_URL")
    key = os.environ.get("DEPLOYER_PRIVATE_KEY")
    if key:
        shape_ok = key.startswith("0x") and len(key) == 66
        rows.append((OK if shape_ok else FAIL, "DEPLOYER_PRIVATE_KEY is set" + ("" if shape_ok else " but is not 0x + 64 hex chars")))
        live_ok &= shape_ok
    else:
        rows.append((WARN, "DEPLOYER_PRIVATE_KEY not set: simulation works, creating a vault does not"))
        live_ok = False

    # Strategy + context
    ctx_public_rpc = None
    try:
        from deploy.config import load_strategy
        from deploy.context import load_context
        cfg = load_strategy(strategy)
        ctx = load_context(cfg.context_name)
        ctx_public_rpc = ctx.public_rpc
        rows.append((OK, f"strategy {strategy.name} valid: chain {cfg.chain_id}, context {cfg.context_name}, vault {cfg.raw['vault']['symbol']}"))
        from deploy.guards import placeholder_accounts_in_config
        ph = placeholder_accounts_in_config(cfg.raw)
        if ph:
            rows.append((WARN, f"{len(ph)} placeholder (anvil) account(s) in the strategy: fine for rehearsal, refused on a live chain"))
            live_ok = False
    except FileNotFoundError as e:
        rows.append((FAIL, f"strategy or context file not found: {e}"))
        dry_run_ok = False
    except Exception as e:
        rows.append((FAIL, f"strategy does not load: {e}"))
        dry_run_ok = False

    # RPC reachability
    target = rpc or ctx_public_rpc
    if target:
        try:
            from web3 import Web3
            w3 = Web3(Web3.HTTPProvider(target, request_kwargs={"timeout": 10}))
            chain = w3.eth.chain_id
            cv = ""
            try:
                cv = str(w3.client_version)
            except Exception:
                pass
            from deploy.guards import is_local_node
            kind = "local fork" if is_local_node(cv) else "live"
            src = "RPC_URL" if rpc else "context public_rpc (dry-run only)"
            rows.append((OK, f"RPC reachable via {src}: chain_id {chain}, {kind} node"))
            if not rpc:
                rehearsal_ok = False
                live_ok = False
            elif kind == "live":
                rehearsal_ok = False
        except Exception as e:
            rows.append((FAIL, f"RPC not reachable ({target.split('//')[-1].split('/')[0]}): {e!r}"))
            dry_run_ok = False
            rehearsal_ok = live_ok = False
    else:
        rows.append((FAIL, "no RPC: set RPC_URL or add public_rpc to the context"))
        dry_run_ok = rehearsal_ok = live_ok = False

    # Foundry
    anvil = shutil.which("anvil") or (str(Path.home() / ".foundry/bin/anvil") if (Path.home() / ".foundry/bin/anvil").exists() else None)
    if anvil:
        rows.append((OK, f"anvil found at {anvil}"))
    else:
        rows.append((WARN, "anvil not found: fork rehearsals need Foundry (https://getfoundry.sh)"))
        rehearsal_ok = False

    print("fusion-vault-launchpad doctor\n")
    for mark, text in rows:
        print(f"  {mark} {text}")
    print("\nAvailable now:")
    print(f"  dry-run (simulation)   {'yes' if dry_run_ok else 'NO'}   python -m deploy {strategy}")
    print(f"  fork rehearsal         {'yes' if rehearsal_ok else 'no '}   needs anvil running and RPC_URL=http://127.0.0.1:<port>")
    print(f"  live deployment        {'yes' if live_ok else 'no '}   needs RPC_URL (private), DEPLOYER_PRIVATE_KEY, real addresses, and a human 'yes'")
    return 0 if dry_run_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
