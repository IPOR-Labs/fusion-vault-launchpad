"""Command-line entry point: ``python -m deploy <strategy.json> [flags]``.

Modes
  (no flag)        dry-run: validate, preview the clone, print every intended call,
                   write .deploy-state/<name>.plan.json. No key, no funds, no state.
  --broadcast      send transactions. Needs RPC_URL and DEPLOYER_PRIVATE_KEY.
                   Against a local fork (anvil) this is a rehearsal; against a live
                   chain it creates a real vault and additionally needs
                   --i-understand-this-is-live.
  --verify-only    read the chain against the JSON using the recorded state.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.config import load_strategy
from deploy.context import load_context
from deploy.guards import is_public_rpc, live_broadcast_problems
from deploy.sdk_session import open_session
from deploy.state import load_state, save_state
from deploy.steps import all_steps
from deploy.verification import verify


def _warn_public_rpc(rpc_url: str) -> None:
    """Broadcast hygiene: a public RPC can confirm so slowly that the client-side timeout
    kills the run mid-sequence, and later hand-sent transactions race nonces."""
    if is_public_rpc(rpc_url):
        host = rpc_url.split("//")[-1].split("/")[0].lower()
        print(f"⚠️  RPC_URL points at a PUBLIC endpoint ({host}). For --broadcast use a private RPC and run "
              f"detached (nohup / background) — public nodes stall confirmations and race nonces. "
              f"If a run is interrupted, re-run WITHOUT --force-restart: completed steps are skipped from state.")


def _artifact_path(state_path: Path, mode: str) -> Path:
    """`<state_dir>/<name>.plan.json` for a dry-run, `.run.json` for a broadcast."""
    suffix = "plan" if mode == "dry-run" else "run"
    return state_path.with_name(f"{state_path.stem}.{suffix}.json")


def _write_artifact(state_path: Path, session) -> Path:
    return session.recorder.write(_artifact_path(state_path, session.recorder.mode))


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="python -m deploy", description="IPOR Fusion PlasmaVault deployer")
    p.add_argument("strategy_json", help="path to strategy JSON")
    p.add_argument("--broadcast", action="store_true", help="actually send transactions")
    p.add_argument("--from-step", type=int, default=None, help="resume from step N")
    p.add_argument("--only-step", type=int, default=None, help="run only step N")
    p.add_argument("--verify-only", action="store_true", help="run verification against existing state")
    p.add_argument("--force-restart", action="store_true", help="discard prior state for this strategy")
    p.add_argument("--i-understand-this-is-live", action="store_true",
                   help="required for --broadcast against any node that is not a local fork")
    # Kept for compatibility with older docs; same meaning as --i-understand-this-is-live.
    p.add_argument("--i-understand-this-is-mainnet", dest="i_understand_this_is_live", action="store_true",
                   help=argparse.SUPPRESS)
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.broadcast:
        _warn_public_rpc(os.environ.get("RPC_URL", ""))
    cfg = load_strategy(args.strategy_json)
    deploy_ctx = load_context(cfg.context_name)
    session = open_session(cfg, deploy_ctx, broadcast=args.broadcast)

    if args.broadcast and not session.is_local_node:
        if not args.i_understand_this_is_live:
            sys.exit(
                f"Broadcast against a LIVE node ({session.client_version or 'unknown client'}, "
                f"chain_id={session.ctx.web3.eth.chain_id}) requires --i-understand-this-is-live. "
                "For a rehearsal, point RPC_URL at an anvil fork."
            )
        problems = live_broadcast_problems(cfg.raw, str(session.signer), session.client_version)
        if problems:
            sys.exit("Refusing to broadcast to a live chain:\n  - " + "\n  - ".join(problems))

    state_path = ROOT / cfg.state_file
    state = load_state(state_path, cfg.raw, force_restart=args.force_restart)

    if args.verify_only:
        if not state.fusion_instance:
            sys.exit("No prior state — nothing to verify.")
        verify(cfg, deploy_ctx, session, state.fusion_instance)
        return

    steps = all_steps()
    for idx, mod in enumerate(steps):
        if args.from_step is not None and idx < args.from_step:
            continue
        if args.only_step is not None and idx != args.only_step:
            continue
        try:
            # `instance` is always read from state: 01_clone populates
            # state.fusion_instance in both modes (dry-run keeps it in memory only).
            mod.run(cfg, deploy_ctx, session, state.fusion_instance, state, args.broadcast)
        except Exception as e:
            print(f"\n!!! Step {mod.NAME} failed: {e!r}")
            # Only a real broadcast has on-chain state worth persisting; a dry-run
            # must NOT write state (a persisted preview clone address would make a
            # later --broadcast skip 01_clone and no-op every step).
            if args.broadcast:
                save_state(state_path, state)
            _write_artifact(state_path, session)
            raise
        if args.broadcast:
            save_state(state_path, state)

    artifact = _write_artifact(state_path, session)
    print(f"Wrote run artifact: {artifact}")

    if not args.broadcast:
        n_actions = len(session.recorder.actions)
        n_steps = len({a.step for a in session.recorder.actions})
        print(f"\nDRY-RUN COMPLETE: {n_actions} planned actions across {n_steps} steps. "
              "Nothing was sent and no state was written.")
        print("  The plan above is what --broadcast would execute. The on-chain verification report")
        print("  runs only after a broadcast, because the vault does not exist yet.")
        print("  Next: rehearse on a local fork (docs/04-deploy.md §4), then review with a human.")
        print("\nDONE.")
        return

    if state.fusion_instance:
        verify(cfg, deploy_ctx, session, state.fusion_instance)

    if args.broadcast:
        save_state(state_path, state)
        inst = state.fusion_instance or {}
        print("\nDeployed FusionInstance:")
        for k in ("plasma_vault", "access_manager", "withdraw_manager", "fee_manager",
                  "rewards_manager", "price_manager", "context_manager"):
            if inst.get(k):
                print(f"  {k:<18} {inst[k]}")
        print(f"State file: {state_path}")
    print("\nDONE.")


if __name__ == "__main__":
    main()
