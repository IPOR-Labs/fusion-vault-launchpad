"""Deploy a set of connected vaults in one run: ``python -m deploy.campaign <campaign.json> [flags]``.

A campaign file lists vaults (each one strategy JSON) and the connections between them:

    {
      "name": "my-vault-set",
      "vaults": [
        {"id": "child",  "strategy": "child-vault.json"},
        {"id": "parent", "strategy": "parent-vault.json"},
        {"id": "remote", "strategy": "remote-vault.json", "blocked": "why it cannot be deployed yet"}
      ],
      "links": [
        {"parent": "parent", "child": "child", "kind": "erc4626", "placeholder": "0x…"},
        {"parent": "parent", "child": "other", "kind": "erc4626", "market": "ERC4626_0001"},
        {"parent": "parent", "child": "remote", "kind": "cross-chain", "blocked": "no cross-chain fuse yet"}
      ]
    }

Strategy paths are relative to the campaign file. Vaults deploy children first. An
`erc4626` link means the parent holds the child's shares: before the parent deploys, every
occurrence of `placeholder` in the parent's strategy (a stand-in ERC4626 used to rehearse
it alone) is replaced by the child's deployed vault address; after the parent deploys, the
child's access manager grants the parent `WHITELIST_ROLE`, so the parent may deposit.
An `erc4626` link with `market` instead of `placeholder` needs no stand-in: the child's
address is appended to that market's substrates in the parent's strategy, with an
ERC4626 price feed for its shares (use it for a second child in the same market). A
`cross-chain` link is drawn but never executed. A blocked vault, or a vault whose child is
blocked through an `erc4626` link, is skipped and shown as blocked.

With ``--signer browser`` one signing page serves the whole run: a map of the vaults and
connections at the top, the steps of the stage being signed on the right.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web3 import Web3

from deploy.browser_signer import load_plan
from deploy.config import load_strategy
from deploy.state import hash_config

LINK_KINDS = ("erc4626", "cross-chain")
CONNECT_STEP = "connect"
WHITELIST_ROLE_ID = 800   # ipor_fusion.config.roles.Roles.WHITELIST_ROLE


@dataclass
class Vault:
    id: str
    strategy: Path
    blocked: str | None = None


@dataclass
class Link:
    parent: str
    child: str
    kind: str
    placeholder: str | None = None
    market: str | None = None
    blocked: str | None = None

    @property
    def id(self) -> str:
        return f"link:{self.parent}<-{self.child}"


@dataclass
class Campaign:
    name: str
    path: Path
    vaults: list[Vault]
    links: list[Link] = field(default_factory=list)

    def vault(self, vid: str) -> Vault:
        return next(v for v in self.vaults if v.id == vid)


def load_campaign(path: str | Path) -> Campaign:
    p = Path(path).resolve()
    raw = json.loads(p.read_text())
    vaults = [Vault(id=v["id"], strategy=(p.parent / v["strategy"]).resolve(), blocked=v.get("blocked")) for v in raw["vaults"]]
    links = [Link(parent=l["parent"], child=l["child"], kind=l["kind"], placeholder=l.get("placeholder"),
                  market=l.get("market"), blocked=l.get("blocked"))
             for l in raw.get("links", [])]
    c = Campaign(name=raw["name"], path=p, vaults=vaults, links=links)
    problems = campaign_problems(c)
    if problems:
        raise ValueError("campaign file: " + "; ".join(problems))
    return c


def campaign_problems(c: Campaign) -> list[str]:
    out = []
    ids = [v.id for v in c.vaults]
    if len(ids) != len(set(ids)):
        out.append("vault ids must be unique")
    for l in c.links:
        if l.parent not in ids or l.child not in ids:
            out.append(f"{l.id} names an unknown vault")
        if l.kind not in LINK_KINDS:
            out.append(f"{l.id}: kind must be one of {LINK_KINDS}")
        if l.kind == "erc4626" and not l.blocked and not ((l.placeholder and Web3.is_address(l.placeholder)) or l.market):
            out.append(f"{l.id}: an erc4626 link needs the stand-in address to replace (placeholder) or the market to add the child to (market)")
    if not out:   # the order is only meaningful once every link names a known vault
        try:
            deploy_order(c)
        except ValueError as e:
            out.append(str(e))
    return out


def deploy_order(c: Campaign) -> list[str]:
    """Vault ids, every child before its parents; ties keep the file order."""
    children = {v.id: [l.child for l in c.links if l.parent == v.id] for v in c.vaults}
    order, mark = [], {}

    def visit(vid, path):
        if mark.get(vid) == "done":
            return
        if mark.get(vid) == "open":
            raise ValueError("connections form a cycle: " + " -> ".join(path + [vid]))
        mark[vid] = "open"
        for ch in children[vid]:
            visit(ch, path + [vid])
        mark[vid] = "done"
        order.append(vid)

    for v in c.vaults:
        visit(v.id, [])
    return order


def blocked_reasons(c: Campaign) -> dict[str, str]:
    """Vault id -> why it cannot be deployed in this run (its own reason, or a blocked child it
    must hold through an erc4626 link)."""
    out: dict[str, str] = {}
    for vid in deploy_order(c):
        v = c.vault(vid)
        if v.blocked:
            out[vid] = v.blocked
            continue
        for l in c.links:
            if l.parent == vid and l.kind == "erc4626" and (l.blocked or l.child in out):
                out[vid] = f"needs {l.child}, which is blocked"
    return out


def stages(c: Campaign) -> list[dict]:
    """The run in order: each vault, then right after a parent, its erc4626 connections."""
    blocked = blocked_reasons(c)
    out = []
    for vid in deploy_order(c):
        out.append({"id": vid, "kind": "vault", "blocked": blocked.get(vid)})
        for l in c.links:
            if l.parent == vid and l.kind == "erc4626":
                why = l.blocked or blocked.get(vid) or blocked.get(l.child)
                out.append({"id": l.id, "kind": "link", "link": l, "blocked": why})
    return out


def resolve_strategy(raw: dict, c: Campaign, vid: str, addresses: dict[str, str]) -> dict:
    """The parent's strategy with every stand-in replaced by its deployed child, and every
    `market` link's child added to that market (substrate + ERC4626 share price feed)."""
    text = json.dumps(raw)
    for l in c.links:
        if l.parent == vid and l.kind == "erc4626" and l.placeholder and l.child in addresses:
            text = re.sub(re.escape(l.placeholder), Web3.to_checksum_address(addresses[l.child]), text, flags=re.IGNORECASE)
    out = json.loads(text)
    for l in c.links:
        if l.parent != vid or l.kind != "erc4626" or not l.market or l.child not in addresses:
            continue
        child = Web3.to_checksum_address(addresses[l.child])
        sub = next((s for s in out.get("substrates", []) if s.get("market") == l.market), None)
        if sub is None:
            raise ValueError(f"{l.id}: {vid} has no substrates for market {l.market} to add {l.child} to")
        if child.lower() not in {str(v).lower() for v in sub["values"]}:
            sub["values"].append(child)
        feeds = out.setdefault("price_feeds", [])
        if child.lower() not in {f["asset"].lower() for f in feeds}:
            feeds.append({"asset": child, "feed_type": "ERC4626PriceFeedFactory", "feed": None, "params": {}})
    return out


# --- plans (for the page: every signature of the whole run, before the first one) ---------

def _plan_for(strategy: Path) -> tuple[list | None, str | None, str]:
    cfg = load_strategy(strategy)
    state_path = ROOT / cfg.state_file
    plan_path = state_path.with_name(f"{state_path.stem}.plan.json")
    planned, why = load_plan(plan_path, hash_config(cfg.raw))
    if planned is None:
        env = {k: v for k, v in os.environ.items() if k != "DEPLOYER_PRIVATE_KEY"}
        log = plan_path.with_name(f"{plan_path.stem}.log")
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w") as f:
            rc = subprocess.run([sys.executable, "-m", "deploy", str(strategy), "--force-restart"], cwd=ROOT, env=env,
                                stdout=f, stderr=subprocess.STDOUT).returncode
        planned, why = load_plan(plan_path, hash_config(cfg.raw)) if rc == 0 else (None, f"dry-run failed (exit {rc}, log {log})")
    return planned, why, cfg.name


def _connect_action(link: Link, parent_addr: str | None, child_am: str | None) -> dict:
    return {"step": CONNECT_STEP, "action": "grantRole(WHITELIST_ROLE)", "key": link.parent,
            "function": "grantRole(uint64,address,uint32)", "target": child_am,
            "args": {"role": "WHITELIST_ROLE", "role_id": WHITELIST_ROLE_ID, "account": parent_addr or f"<{link.parent} vault>", "delay": 0},
            "note": f"lets {link.parent} deposit into {link.child}"}


# --- the run --------------------------------------------------------------------------------

class CampaignRun:
    def __init__(self, c: Campaign, args):
        self.c, self.args = c, args
        self.stages = stages(c)
        self.cfgs = {v.id: load_strategy(v.strategy) for v in c.vaults if v.id not in blocked_reasons(c)}
        self.meta_cfg = {}
        for v in c.vaults:   # blocked vaults are still drawn, so read what the file says even if it is not deployable
            try:
                self.meta_cfg[v.id] = json.loads(v.strategy.read_text())
            except (OSError, json.JSONDecodeError):
                self.meta_cfg[v.id] = {}
        self.state_dir = ROOT / ".deploy-state"
        self.link_state_path = self.state_dir / f"{c.name}.campaign.json"
        self.link_state = {} if args.force_restart or not self.link_state_path.exists() else json.loads(self.link_state_path.read_text())
        self.addresses: dict[str, str] = {}
        self.instances: dict[str, dict] = {}
        self.status: dict[str, str] = {s["id"]: ("blocked" if s["blocked"] else "waiting") for s in self.stages}
        self.plans: dict[str, list | None] = {}
        self.plan_notes: dict[str, str | None] = {}
        self.current: str | None = None
        self.finished: dict | None = None            # {"ok": bool} once the run is over
        self.signer = None

    # page view of the whole set
    def view(self) -> dict:
        def totals(sid):
            if self.signer and sid == self.signer.stage_id:
                b = self.signer.board()
            else:
                b = self.signer._archive.get(sid) if self.signer else None
            if b:
                return b["signed"], b["total"]
            planned = self.plans.get(sid) or []
            return 0, len(planned)
        order = [s["id"] for s in self.stages if s["kind"] == "vault"]
        vaults = []
        for v in self.c.vaults:
            raw = self.meta_cfg.get(v.id, {})
            signed, total = totals(v.id)
            vaults.append({"id": v.id, "name": raw.get("vault", {}).get("name"), "symbol": raw.get("vault", {}).get("symbol"),
                           "chain": raw.get("chain", {}).get("id"), "status": self.status[v.id],
                           "blocked": next(s["blocked"] for s in self.stages if s["id"] == v.id),
                           "address": self.addresses.get(v.id), "order": order.index(v.id) + 1, "signed": signed, "total": total})
        links = []
        for l in self.c.links:
            st = next((s for s in self.stages if s["id"] == l.id), None)
            signed, total = totals(l.id) if st else (0, 0)
            links.append({"id": l.id, "parent": l.parent, "child": l.child, "kind": l.kind,
                          "status": self.status.get(l.id, "blocked" if l.kind != "erc4626" else "waiting"),
                          "blocked": l.blocked or (st["blocked"] if st else "drawn only: this pipeline has no cross-chain step"),
                          "executable": st is not None, "signed": signed, "total": total})
        return {"name": self.c.name, "current": self.current, "vaults": vaults, "links": links, "finished": self.finished}

    def _vault_meta(self, vid: str, cfg) -> dict:
        from deploy.cli import _signer_meta_base
        m = _signer_meta_base(cfg, ROOT / cfg.state_file, self.args.force_restart)
        m.update({"stage_id": vid, "kind": "vault", "planned": self.plans.get(vid), "plan_note": self.plan_notes.get(vid)})
        return m

    def _link_meta(self, link: Link) -> dict:
        child_inst = self.instances.get(link.child) or {}
        return {"stage_id": link.id, "kind": "link", "strategy": f"{link.parent} → {link.child}",
                "vault": {"name": f"Connect {link.parent} to {link.child}", "symbol": None},
                "chain": {"id": self.cfgs[link.child].chain_id}, "steps": [CONNECT_STEP],
                "planned": [_connect_action(link, self.addresses.get(link.parent), child_inst.get("access_manager"))],
                "earlier_steps": {}, "link": {"parent": link.parent, "child": link.child}}

    def prepare_plans(self) -> None:
        todo = [s["id"] for s in self.stages if s["kind"] == "vault" and not s["blocked"]]
        print(f"[campaign] dry-running {len(todo)} vault(s) so the page can list every signature …")
        with ThreadPoolExecutor(max_workers=4) as ex:
            for vid, (planned, why, _) in zip(todo, ex.map(lambda i: _plan_for(self.c.vault(i).strategy), todo)):
                self.plans[vid], self.plan_notes[vid] = planned, why
        for s in self.stages:
            if s["kind"] == "link" and not s["blocked"]:
                self.plans[s["id"]] = [_connect_action(s["link"], None, None)]

    def run(self) -> int:
        a = self.args
        order = [s["id"] for s in self.stages]
        print(f"[campaign] {self.c.name}: " + " → ".join(f"{i}{' (blocked)' if self.status[i] == 'blocked' else ''}" for i in order))
        if not a.broadcast:
            self.prepare_plans()
            for vid, planned in self.plans.items():
                print(f"  {vid:<24} {len(planned or [])} planned action(s)" + (f"  ({self.plan_notes[vid]})" if self.plan_notes.get(vid) else ""))
            print("DRY-RUN COMPLETE. Parents are planned against their stand-in; the broadcast swaps in the deployed children.")
            return 0
        if a.signer == "browser":
            self.prepare_plans()
            self._start_page()
        rc = 0
        try:
            for s in self.stages:
                if s["blocked"]:
                    print(f"[campaign] skip {s['id']}: {s['blocked']}")
                    continue
                self.current = s["id"]
                self.status[s["id"]] = "current"
                ok = self._run_vault(s["id"]) if s["kind"] == "vault" else self._run_link(s["link"])
                self.status[s["id"]] = "done" if ok else "failed"
                if self.signer:
                    self.signer.archive_current()
                if not ok:
                    rc = 1
                    for later in self.stages[self.stages.index(s) + 1:]:
                        if self.status[later["id"]] == "waiting":
                            self.status[later["id"]] = "blocked"
                            later["blocked"] = f"stopped: {s['id']} failed"
                    break
            self.current = None
        finally:
            self.finished = {"ok": rc == 0}
            if self.signer:
                self.signer.linger(a.signer_linger * 60)
        return rc

    def _start_page(self) -> None:
        from deploy.browser_signer import BrowserSigner, SigningQueue
        first = next(s["id"] for s in self.stages if s["kind"] == "vault" and not s["blocked"])
        cfg = self.cfgs[first]
        self.signer = BrowserSigner(SigningQueue(cfg.chain_id, os.environ.get("DEPLOYER_ADDRESS") or None),
                                    port=self.args.signer_port, meta={})
        self.signer.campaign_source = self.view
        self.signer.begin_stage(self._vault_meta(first, cfg))
        self.signer.start()

    def _cli_args(self, strategy_path: Path) -> list[str]:
        a = self.args
        out = [str(strategy_path), "--broadcast", "--signer", a.signer, "--signer-port", str(a.signer_port)]
        if a.force_restart:
            out.append("--force-restart")
        if getattr(a, "batch", False):
            out.append("--batch")
        if a.i_understand_this_is_live:
            out.append("--i-understand-this-is-live")
        return out

    def _run_vault(self, vid: str) -> bool:
        from deploy import cli
        v, cfg = self.c.vault(vid), self.cfgs[vid]
        resolved = resolve_strategy(cfg.raw, self.c, vid, self.addresses)
        path = v.strategy
        if resolved != cfg.raw:
            path = self.state_dir / self.c.name / f"{vid}.resolved.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(resolved, indent=2))
            print(f"[campaign] {vid}: stand-ins replaced by deployed children → {path}")
        rcfg = load_strategy(path)
        self._rpc_for(rcfg.chain_id)
        print(f"\n[campaign] ===== {vid} ({rcfg.name}) =====")
        # the parent grants itself WHITELIST_ROLE on each child it holds, inside its own run (its own
        # batch with --batch), so no child has to be revisited once the parent exists
        grants = [self.instances[l.child]["access_manager"] for l in self.c.links
                  if l.parent == vid and l.kind == "erc4626" and not l.blocked and l.child in self.instances]
        os.environ["DEPLOY_LINK_GRANTS"] = json.dumps(grants)
        try:
            result = cli.main(self._cli_args(path), shared_signer=self.signer,
                              signer_meta=self._vault_meta(vid, rcfg) if self.signer else None)
        except (Exception, SystemExit) as e:
            print(f"[campaign] {vid} failed: {e!r}")
            return False
        finally:
            os.environ.pop("DEPLOY_LINK_GRANTS", None)
        inst = (result or {}).get("instance") or {}
        if not inst.get("plasma_vault"):
            return False
        self.instances[vid], self.addresses[vid] = inst, inst["plasma_vault"]
        ver = (result or {}).get("verification") or {}
        return not ver.get("fail")

    def _run_link(self, link: Link) -> bool:
        from ipor_fusion.core.access import AccessManager
        from deploy.context import load_context
        from deploy.plan import RunRecorder
        from deploy.sdk_session import open_session
        child_cfg = load_strategy(self.c.vault(link.child).strategy)
        parent, am = self.addresses[link.parent], self.instances[link.child]["access_manager"]
        self._rpc_for(child_cfg.chain_id)
        print(f"\n[campaign] ===== connect {link.parent} → {link.child}: WHITELIST_ROLE for {parent} on {am} =====")
        if self.signer:
            self.signer.begin_stage(self._link_meta(link))
        session = open_session(child_cfg, load_context(child_cfg.context_name, chain_id=child_cfg.chain_id), broadcast=True, signer_mode=self.args.signer,
                               signer_port=self.args.signer_port, browser_signer=self.signer)
        rec = RunRecorder(strategy=link.id, config_hash="", mode="broadcast", chain_id=child_cfg.chain_id, rpc_source="",
                          signer=str(session.signer), gas_multiplier=1.0, context=child_cfg.context_name)
        if self.signer:
            self.signer.install(session.ctx, recorder=rec, instance_source=lambda: self.instances.get(link.child),
                                label_source=lambda: f"connect · grantRole(WHITELIST_ROLE) → {link.parent}")
            self.signer.step_started(CONNECT_STEP)
        action = rec.add(**_connect_action(link, parent, am))
        access = AccessManager(session.ctx, am)
        try:
            has, _ = session.ctx.web3.eth.contract(address=Web3.to_checksum_address(am), abi=[{
                "name": "hasRole", "type": "function", "stateMutability": "view",
                "inputs": [{"name": "roleId", "type": "uint64"}, {"name": "account", "type": "address"}],
                "outputs": [{"name": "isMember", "type": "bool"}, {"name": "executionDelay", "type": "uint32"}]}]
            ).functions.hasRole(WHITELIST_ROLE_ID, Web3.to_checksum_address(parent)).call()
            if has:
                print(f"[campaign] {link.parent} already holds WHITELIST_ROLE on {link.child}; nothing to send")
                action.skipped, action.note = True, "already granted"
            else:
                receipt = access.grant_role(WHITELIST_ROLE_ID, Web3.to_checksum_address(parent), 0).send()
                action.executed, action.tx_hash, action.gas_used = True, receipt["transactionHash"].hex(), int(receipt["gasUsed"])
                print(f"[campaign]   tx {action.tx_hash}")
            self.link_state[link.id] = {"parent": parent, "child_access_manager": am, "tx_hash": action.tx_hash}
            self.link_state_path.parent.mkdir(parents=True, exist_ok=True)
            self.link_state_path.write_text(json.dumps(self.link_state, indent=2))
        except Exception as e:
            print(f"[campaign] connect {link.id} failed: {e!r}")
            if self.signer:
                self.signer.step_failed(CONNECT_STEP, str(e))
                self.signer.finish({"ok": False, "failed_step": CONNECT_STEP, "error": str(e), "state_file": str(self.link_state_path)})
            return False
        if self.signer:
            self.signer.step_finished(CONNECT_STEP)
            self.signer.finish({"ok": True, "link": {"parent": parent, "child_access_manager": am, "tx_hash": action.tx_hash},
                                "state_file": str(self.link_state_path)})
        return True

    @staticmethod
    def _rpc_for(chain_id: int) -> None:
        """RPC_URL_<chainId> wins over RPC_URL, so a campaign across chains needs one endpoint per chain."""
        if not hasattr(CampaignRun, "_base_rpc"):
            CampaignRun._base_rpc = os.environ.get("RPC_URL", "")
        os.environ["RPC_URL"] = os.environ.get(f"RPC_URL_{chain_id}") or CampaignRun._base_rpc


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="python -m deploy.campaign", description="Deploy a set of connected Fusion vaults")
    p.add_argument("campaign_json")
    p.add_argument("--broadcast", action="store_true", help="send transactions (without it: dry-run every vault)")
    p.add_argument("--signer", choices=["key", "browser", "impersonate"], default="key")
    p.add_argument("--signer-port", type=int, default=8789)
    p.add_argument("--signer-linger", type=float, default=30, metavar="MIN")
    p.add_argument("--force-restart", action="store_true", help="discard prior state for every vault and connection")
    p.add_argument("--batch", action="store_true", help="sign each vault as one atomic batch in the wallet (see python -m deploy --help)")
    p.add_argument("--i-understand-this-is-live", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    import deploy.sdk_session  # noqa: F401  loads .env, so DEPLOYER_ADDRESS pins the signing page's account
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    return CampaignRun(load_campaign(args.campaign_json), args).run()


if __name__ == "__main__":
    raise SystemExit(main())
