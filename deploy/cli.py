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
import json
import os
from web3 import Web3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.config import load_strategy
from deploy.context import load_context
from deploy.fuse_resolver import apply_whitelist_resolution
from deploy.address_book import check_context_addresses
from deploy.browser_signer import load_plan
from deploy.guards import live_broadcast_problems, warn_public_rpc
from deploy.sdk_session import open_session
from deploy.chain_guards import resume_error, resume_problems
from deploy.state import hash_config, load_state, save_state
from deploy.steps import all_steps
from deploy.rehearsal import rehearse
from deploy.verification import verify


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
    p.add_argument("--signer", choices=["key", "browser", "impersonate"], default="key",
                   help="key: sign with DEPLOYER_PRIVATE_KEY from .env (default); browser: sign each transaction in a wallet through a local page; "
                        "impersonate: fork only, the node signs for DEPLOYER_ADDRESS so the rehearsal runs as the production deployer")
    p.add_argument("--signer-port", type=int, default=8789, help="port of the local signing page (--signer browser)")
    p.add_argument("--signer-linger", type=float, default=30, metavar="MIN",
                   help="--signer browser: keep the page up this many minutes after the run so the record stays browsable (0 = exit at once)")
    p.add_argument("--assign-alpha", action="store_true",
                   help="grant only the alpha roles (ALPHA, balance and rewards updaters) listed in roles.grants, on an existing vault; the base run defers them")
    p.add_argument("--from-step", type=int, default=None, help="resume from step N")
    p.add_argument("--only-step", type=int, default=None, help="run only step N")
    p.add_argument("--verify-only", action="store_true", help="run verification against existing state")
    p.add_argument("--rehearse", action="store_true",
                   help="after a --broadcast on a local fork: fund, deposit, execute the rehearsal script, check accounting, withdraw")
    p.add_argument("--rehearse-only", action="store_true",
                   help="run only the rehearsal stage against the vault in the recorded state (local fork, needs RPC_URL and the fork key)")
    p.add_argument("--force-restart", action="store_true", help="discard prior state for this strategy")
    p.add_argument("--batch", action="store_true",
                   help="--signer browser: simulate the whole vault on a fork, then sign it as a few transactions: the clone and "
                        "AccessManager.multicall batches carrying every setup call (no extra contract, no wallet batching needed)")
    p.add_argument("--i-understand-this-is-live", action="store_true",
                   help="required for --broadcast against any node that is not a local fork")
    # Kept for compatibility with older docs; same meaning as --i-understand-this-is-live.
    p.add_argument("--i-understand-this-is-mainnet", dest="i_understand_this_is_live", action="store_true",
                   help=argparse.SUPPRESS)
    return p.parse_args(argv)


def _clone_block(session, state) -> int | None:
    """Block of the recorded clone transaction (where the permission audit starts), if known."""
    rec = next((r for r in state.completed_steps if r.step == "01_clone" and r.tx_hashes), None)
    if not rec:
        return None
    h = rec.tx_hashes[0] if rec.tx_hashes[0].startswith("0x") else "0x" + rec.tx_hashes[0]
    try:
        return int(session.ctx.web3.eth.get_transaction_receipt(h)["blockNumber"])
    except Exception:
        return None


def _install_capture(session) -> None:
    """DEPLOY_CAPTURE (set by the batch simulation): append every sent call, in order, with the step
    that sent it, so the live run can replay them as one batch."""
    path = os.environ.get("DEPLOY_CAPTURE")
    if not path:
        return
    orig = session.ctx.send

    def capture_send(to, data):
        receipt = orig(to, data)
        last = session.recorder.actions[-1] if session.recorder.actions else None
        hexdata = data if isinstance(data, str) else "0x" + bytes(data).hex()
        with open(path, "a") as f:
            f.write(json.dumps({"to": Web3.to_checksum_address(to), "data": hexdata if hexdata.startswith("0x") else "0x" + hexdata,
                                "value": "0x0", "gas_used": int(receipt["gasUsed"]),
                                "step": last.step if last else "",
                                "label": (f"{last.step} · {last.function or last.action}" if last and (last.target or "").lower() == str(to).lower()
                                          else f"{last.step if last else ''} · call to {Web3.to_checksum_address(to)} (outside the vault, e.g. a price-feed factory)")}) + "\n")
        return receipt

    session.ctx.send = capture_send


def _grant_link_whitelist(session, state) -> None:
    """DEPLOY_LINK_GRANTS (set by deploy.campaign for a parent vault): let this vault deposit into the
    children it holds, by granting it WHITELIST_ROLE on each child's access manager, in this run."""
    from ipor_fusion.core.access import AccessManager
    from deploy.campaign import WHITELIST_ROLE_ID
    ams = json.loads(os.environ.get("DEPLOY_LINK_GRANTS") or "[]")
    vault = Web3.to_checksum_address(state.fusion_instance["plasma_vault"])
    for am in ams:
        am = Web3.to_checksum_address(am)
        has, _ = session.ctx.web3.eth.contract(address=am, abi=[{
            "name": "hasRole", "type": "function", "stateMutability": "view",
            "inputs": [{"name": "roleId", "type": "uint64"}, {"name": "account", "type": "address"}],
            "outputs": [{"name": "isMember", "type": "bool"}, {"name": "executionDelay", "type": "uint32"}]}]
        ).functions.hasRole(WHITELIST_ROLE_ID, vault).call()
        rec = session.recorder.add("12b_link_whitelist", action="grantRole", key=am, target=am,
                                   function="grantRole(uint64,address,uint32)", args={"role": "WHITELIST_ROLE", "account": vault})
        if has:
            rec.skipped, rec.note = True, "already granted"
            continue
        receipt = AccessManager(session.ctx, am).grant_role(WHITELIST_ROLE_ID, vault, 0).send()
        rec.executed, rec.tx_hash, rec.gas_used = True, receipt["transactionHash"].hex(), int(receipt["gasUsed"])
        print(f"[link] {vault} may now deposit into the child behind {am}: tx {rec.tx_hash}")


def _run_batch(cfg, session, state_path: Path, signer, linger) -> bool:
    """Simulate this vault on a fork, then sign it as a few transactions: the clone, AccessManager
    multicalls carrying every vault setup call, and any call outside the vault (feed factories, a
    child's AccessManager). See deploy/batch.py."""
    from deploy import batch
    from deploy.plan import Action
    w3 = session.ctx.web3
    rpc = os.environ.get("RPC_URL", "")
    link_grants = json.loads(os.environ.get("DEPLOY_LINK_GRANTS") or "[]")
    try:
        sim = batch.simulate(Path(cfg.path).resolve(), rpc, str(session.signer), link_grants)
        inst = sim.state.get("fusion_instance") or {}
        if inst.get("plasma_vault") and w3.eth.get_code(Web3.to_checksum_address(inst["plasma_vault"])):
            raise RuntimeError(f"the simulated vault address {inst['plasma_vault']} is already taken on the live chain; run again")
        am = inst["access_manager"].lower()
        managed = {v for k, v in inst.items() if k not in ("access_manager", "initial_owner") and v}
        managed |= {t for t, a in sim.authority.items() if a.lower() == am}     # e.g. the vault's price oracle
        limit = batch.batch_gas_limit(int(w3.eth.chain_id), int(w3.eth.get_block("latest")["gasLimit"]))
        txs = batch.access_manager_txs(sim.calls, inst["access_manager"], managed, limit)
        print(f"[batch] {len(sim.calls)} calls → {len(txs)} transaction(s): " + ", ".join(t["label"] for t in txs))
        signer.step_started("batch")
        hashes_per_call: list[str] = []
        for i, tx in enumerate(txs, 1):
            h = signer.send_packed(tx, f"{i}/{len(txs)} · {tx['label']}")
            r = w3.eth.wait_for_transaction_receipt(h, timeout=900)
            if int(r["status"]) != 1:
                raise RuntimeError(f"transaction {h} reverted; nothing in it took effect")
            hashes_per_call += [h] * len(tx["calls"])
            if i == 1 and not w3.eth.get_code(Web3.to_checksum_address(inst["plasma_vault"])):
                raise RuntimeError(f"the clone was mined but the vault is not at the simulated address {inst['plasma_vault']}: "
                                   "the factory moved between simulation and signing. Nothing else was sent; re-run to simulate again.")
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(batch.adopt(sim, hashes_per_call), indent=2))
        by_step: dict[str, str] = {}
        for c, h in zip(sim.calls, hashes_per_call):
            by_step.setdefault(c.get("step") or "", h)
        for a in sim.actions:
            act = Action(**{k: a.get(k) for k in Action.__slots__ if k in a})
            if act.executed:
                act.tx_hash = by_step.get(act.step, act.tx_hash)
            session.recorder.actions.append(act)
        signer.step_finished("batch")
        print(f"[batch] {len(sim.calls)} calls mined in {len(txs)} transaction(s)")
        return True
    except Exception as e:
        print(f"\n!!! batch failed: {e}")
        signer.step_failed("batch", str(e))
        signer.finish({"ok": False, "failed_step": "batch", "error": str(e), "state_file": str(state_path)})
        linger()
        raise


def _fresh_plan(cfg, state_path: Path) -> tuple[list | None, str | None]:
    """The dry-run plan for this exact strategy file, running the dry-run first when the
    artifact is missing or stale, so the signing page can list every signature up front."""
    plan_path = _artifact_path(state_path, "dry-run")
    planned, why = load_plan(plan_path, hash_config(cfg.raw))
    if planned is not None:
        return planned, None
    print(f"[browser-signer] {why}; running the dry-run first so the page can show every signature")
    env = {k: v for k, v in os.environ.items() if k != "DEPLOYER_PRIVATE_KEY"}
    log = plan_path.with_name(f"{plan_path.stem}.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as f:
        # --force-restart: plan the whole vault even if a fork run left state behind (a dry-run never writes state)
        rc = subprocess.run([sys.executable, "-m", "deploy", str(cfg.path), "--force-restart"], cwd=ROOT, env=env,
                            stdout=f, stderr=subprocess.STDOUT).returncode
    if rc != 0:
        return None, f"the dry-run failed (exit {rc}, log {log}); the page shows transactions as they come, without the full list"
    return load_plan(plan_path, hash_config(cfg.raw))


def _signer_meta(cfg, deploy_ctx, state_path: Path, force_restart: bool = False) -> dict:
    """What the signing page shows before the first transaction: the vault, the chain, the
    factory, every planned signature per step, and steps an earlier run already completed."""
    planned, note = _fresh_plan(cfg, state_path)
    return {**_signer_meta_base(cfg, state_path, force_restart, deploy_ctx), "planned": planned, "plan_note": note}


def _signer_meta_base(cfg, state_path: Path, force_restart: bool = False, deploy_ctx=None) -> dict:
    """The page metadata without the plan (the campaign runner brings its own plans)."""
    deploy_ctx = deploy_ctx or load_context(cfg.context_name)
    earlier: dict[str, list[str]] = {}
    if state_path.exists() and not force_restart:
        try:
            prior = json.loads(state_path.read_text())
            if prior.get("config_hash") == hash_config(cfg.raw):
                earlier = {r["step"]: r.get("tx_hashes", []) for r in prior.get("completed_steps", [])}
        except (OSError, json.JSONDecodeError):
            pass
    v = cfg.raw["vault"]
    return {
        "strategy": cfg.name,
        "vault": {"name": v.get("name"), "symbol": v.get("symbol"), "underlying": v.get("underlying"),
                  "decimals": v.get("decimals")},
        "chain": {"id": cfg.chain_id, "context": cfg.context_name},
        "factory": deploy_ctx.fusion_factory,
        "fee_package_index": v.get("dao_fee_package_index"),
        "steps": [m.NAME for m in all_steps()],
        "earlier_steps": earlier,
    }


def main(argv=None, *, shared_signer=None, signer_meta=None):
    """Run one strategy. `deploy.campaign` calls this once per vault with a `shared_signer`
    (one signing page for the whole set) and the stage's `signer_meta`; then the page is
    neither started nor kept open here, and the result is returned instead of printed only."""
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.broadcast:
        warn_public_rpc(os.environ.get("RPC_URL", ""))
    cfg = load_strategy(args.strategy_json)
    deploy_ctx = load_context(cfg.context_name)
    # DEPLOY_STATE_PATH: the batch simulation keeps its fork state away from the real state file
    state_path = Path(os.environ["DEPLOY_STATE_PATH"]) if os.environ.get("DEPLOY_STATE_PATH") else ROOT / cfg.state_file
    browser = args.signer == "browser" and args.broadcast
    if shared_signer is not None:
        shared_signer.begin_stage(signer_meta or _signer_meta(cfg, deploy_ctx, state_path, args.force_restart))
    # The rehearsal signs transactions with the fork key, so it opens a broadcast-capable session.
    session = open_session(cfg, deploy_ctx, broadcast=args.broadcast or args.rehearse_only,
                           signer_mode=args.signer, signer_port=args.signer_port, browser_signer=shared_signer,
                           signer_meta=_signer_meta(cfg, deploy_ctx, state_path, args.force_restart)
                           if browser and shared_signer is None else None)
    signer = session.browser_signer
    session.options = {"assign_alpha": args.assign_alpha}
    if args.assign_alpha and not state_path.exists():
        sys.exit("--assign-alpha needs a deployed vault: no state file at " + str(state_path))
    linger = (lambda: None) if shared_signer is not None else (lambda: signer.linger(args.signer_linger * 60))
    if (args.rehearse or args.rehearse_only) and not session.is_local_node:
        sys.exit("--rehearse / --rehearse-only impersonate accounts and move time: they run only against a local fork "
                 f"(anvil/hardhat); this node reports {session.client_version or 'unknown client'}.")

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

    # The FuseWhitelist decides which fuse address is current; ipor-abi supplies and cross-checks the rest.
    apply_whitelist_resolution(cfg.raw, deploy_ctx, session.ctx.web3)
    try:
        check_context_addresses(deploy_ctx, session.ctx.web3)
    except RuntimeError as e:
        sys.exit(str(e))

    state = load_state(state_path, cfg.raw, force_restart=args.force_restart)
    # Resume only a vault this chain really has: the recorded clone must be mined here and the
    # vault must have code. Stops a run from configuring addresses from an old fork or an unsent clone.
    w3 = session.ctx.web3

    def _receipt(h):
        try:
            return dict(w3.eth.get_transaction_receipt(h))
        except Exception:
            return None
    problems = resume_problems(state, _receipt, lambda a: bytes(w3.eth.get_code(a)))
    if problems:
        msg = resume_error(state_path, problems)
        if signer:
            signer.step_failed("01_clone", msg)
            signer.finish({"ok": False, "failed_step": "01_clone", "error": msg, "state_file": str(state_path)})
            linger()
        sys.exit(msg)
    if signer:
        # label every wallet prompt with the step/action the recorder registered just before the send
        signer.install(session.ctx, recorder=session.recorder, instance_source=lambda: state.fusion_instance, label_source=lambda: (
            f"{session.recorder.actions[-1].step} · {session.recorder.actions[-1].function or session.recorder.actions[-1].action}"
            if session.recorder.actions else "transaction"))

    _install_capture(session)
    batched = False
    if (args.batch and signer and args.broadcast and not args.assign_alpha and not args.rehearse
            and args.from_step is None and args.only_step is None and not state.fusion_instance):
        batched = _run_batch(cfg, session, state_path, signer, linger)
        state = load_state(state_path, cfg.raw)

    if args.verify_only:
        if not state.fusion_instance:
            sys.exit("No prior state — nothing to verify.")
        verify(cfg, deploy_ctx, session, state.fusion_instance, _clone_block(session, state))
        return

    if args.rehearse_only:
        if not state.fusion_instance:
            sys.exit("No prior state — deploy on the fork first (--broadcast), then rehearse.")
        report = rehearse(cfg, deploy_ctx, session, state.fusion_instance, state_path)
        if report["fail"]:
            sys.exit(f"REHEARSAL FAILED: {len(report['fail'])} check(s) failed — the vault is not verified.")
        return

    steps = [] if batched else all_steps()
    for idx, mod in enumerate(steps):
        if args.from_step is not None and idx < args.from_step:
            continue
        if args.only_step is not None and idx != args.only_step:
            continue
        if args.assign_alpha and mod.NAME != "11_roles":
            continue
        if signer:
            signer.step_started(mod.NAME)
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
            if signer:
                signer.step_failed(mod.NAME, str(e))
                signer.finish({"ok": False, "failed_step": mod.NAME, "error": str(e), "state_file": str(state_path)})
                linger()
            raise
        if signer:
            signer.step_finished(mod.NAME)
        if args.broadcast:
            save_state(state_path, state)

    if args.broadcast and not batched and state.fusion_instance:
        _grant_link_whitelist(session, state)
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

    vreport = None
    if state.fusion_instance:
        vreport = verify(cfg, deploy_ctx, session, state.fusion_instance, _clone_block(session, state))
        if signer:
            signer.finish({"ok": not vreport["fail"], "verification": vreport, "state_file": str(state_path),
                           "run_artifact": str(artifact)})
        if args.rehearse:
            rreport = rehearse(cfg, deploy_ctx, session, state.fusion_instance, state_path)
            if vreport["fail"] or rreport["fail"]:
                save_state(state_path, state)
                linger()
                sys.exit(f"NOT VERIFIED: verification fails={len(vreport['fail'])}, rehearsal fails={len(rreport['fail'])}.")

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
    if signer:
        linger()
    return {"instance": state.fusion_instance, "verification": vreport, "state_file": str(state_path)}


if __name__ == "__main__":
    main()
