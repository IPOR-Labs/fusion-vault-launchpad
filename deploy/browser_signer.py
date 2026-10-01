"""Sign pipeline transactions with a browser wallet instead of a private key.

`python -m deploy <spec> --broadcast --signer browser` starts a small local web
page (default http://127.0.0.1:8789). The pipeline builds every transaction
exactly as it would for a key (`Web3Context.build_transaction`: gas estimate,
revert check), hands the unsigned transaction to the page, and the operator
confirms it in Rabby / MetaMask / any injected wallet. The page returns the
transaction hash, the pipeline waits for the receipt and continues: same steps,
same state file, same verification report. One transaction at a time, in order.

The page also shows the whole run up front: the dry-run plan
(`.deploy-state/<name>.plan.json`) lists every signature per step, and a
progress axis marks which steps are done, which one is signing now and which
are still ahead. Completed steps stay browsable, with their transaction hashes.
After the last step the page keeps serving the result until the operator closes
it (or `linger` times out), so the record can be read after the pipeline ends.

Split: `sanitize_for_wallet`, `load_plan`, `build_board` and `SigningQueue` are
pure and unit-tested; `BrowserSigner` owns the HTTP server thread, the
`Web3Context.send` override and the step lifecycle hooks the CLI calls.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field, is_dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from web3 import Web3

PAGE = Path(__file__).resolve().parent.parent / "tools" / "browser_signer" / "index.html"
WALLET_FIELDS = ("from", "to", "data", "value", "gas", "chainId")
TOKEN_PLACEHOLDER = "__SIGNER_TOKEN__"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def sanitize_for_wallet(tx: dict) -> dict:
    """The dict handed to `eth_sendTransaction`: the wallet sets nonce and fees itself,
    so only sender, target, calldata, value, gas limit and chain go across, hex-encoded."""
    out: dict[str, Any] = {}
    for k in WALLET_FIELDS:
        if k not in tx and k != "value":
            continue
        v = tx.get(k, 0)
        if k in ("gas", "chainId", "value"):
            out[k] = hex(int(v))
        elif k == "data":
            out[k] = v if isinstance(v, str) else "0x" + bytes(v).hex()
        else:
            out[k] = Web3.to_checksum_address(v)
    return out


# --- plan + progress board ---------------------------------------------------------

def _identity(a: dict) -> tuple:
    return (a.get("step"), a.get("action"), a.get("key", ""), json.dumps(a.get("args", {}), sort_keys=True, default=str))


def _loose_identity(a: dict) -> tuple:
    return (a.get("step"), a.get("action"), a.get("key", ""))


def load_plan(path: Path, config_hash: str) -> tuple[list[dict] | None, str | None]:
    """Planned actions from a dry-run artifact, or (None, reason) when it is missing or
    was made from a different version of the strategy file."""
    if not path.exists():
        return None, f"no dry-run plan at {path}"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return None, f"unreadable dry-run plan {path}: {e}"
    if data.get("manifest", {}).get("config_hash") != config_hash:
        return None, f"dry-run plan {path.name} was made from a different version of the strategy file"
    actions = [a for a in data.get("actions", []) if not a.get("skipped")]
    if not actions:   # a dry-run over an existing state file plans only what is left; not a plan of the vault
        return None, f"dry-run plan {path.name} is empty (made while a state file marked every step done)"
    return actions, None


def _action_view(a: dict) -> dict:
    return {k: a.get(k) for k in ("step", "action", "key", "function", "target", "args", "note", "optional")}


def build_board(step_names: list[str], planned: list[dict] | None, run_actions: list[dict],
                sends: dict[int, dict], *, current: str | None, failed: dict | None,
                finished_steps: set[str], earlier_steps: dict[str, list[str]]) -> dict:
    """Merge the plan with what the run has done so far, per step.

    `run_actions` are the recorder's actions in order (dicts); `sends` maps a run
    action index to what the wallet did for it ({"hash", "error", "pending"}).
    A step is `done`, `current`, `failed`, `upcoming`, or `earlier` (completed by a
    previous run of the same state file). Each action carries a status: signed /
    confirming / waiting / rejected / skipped / preparing / not-needed / upcoming / earlier.
    Run actions are matched to planned ones by the plan-diff identity, falling back to
    (step, action, key) when only the arguments differ (e.g. addresses a fork mints).
    """
    planned = planned or []
    by_id: dict[tuple, int] = {}
    by_loose: dict[tuple, list[int]] = {}
    for i, a in enumerate(planned):
        by_id.setdefault(_identity(a), i)
        by_loose.setdefault(_loose_identity(a), []).append(i)
    plan_to_run: dict[int, int] = {}
    run_only: list[int] = []
    for ri, a in enumerate(run_actions):
        pi = by_id.get(_identity(a))
        if pi is None or pi in plan_to_run:
            pi = next((j for j in by_loose.get(_loose_identity(a), []) if j not in plan_to_run), None)
        if pi is None:
            run_only.append(ri)
        else:
            plan_to_run[pi] = ri

    def run_row(ri: int, step_closed: bool, planned_flag: bool) -> dict:
        a, s = run_actions[ri], sends.get(ri, {})
        if a.get("executed"):
            status = "signed"
        elif s.get("skipped"):
            status = "skipped"
        elif s.get("error"):
            status = "rejected"
        elif s.get("hash"):
            status = "sent" if step_closed else "confirming"   # the step moved on: the receipt came back
        elif s.get("pending"):
            status = "waiting"
        else:
            status = "skipped" if step_closed or a.get("skipped") else "preparing"
        return {**_action_view(a), "status": status, "planned": planned_flag, "index": ri, "optional": bool(a.get("optional")),
                "tx_hash": a.get("tx_hash") or s.get("hash"), "gas_used": a.get("gas_used"), "error": s.get("error")}

    steps = []
    for name in step_names:
        # this run wins over an earlier one: a step can run again (e.g. 11_roles with --assign-alpha)
        if failed and failed.get("step") == name:
            status = "failed"
        elif name == current:
            status = "current"
        elif name in finished_steps and not (name in earlier_steps and not any(a.get("step") == name for a in run_actions)):
            status = "done"
        elif name in earlier_steps:
            status = "earlier"
        else:
            status = "upcoming"
        closed = status in ("done", "failed", "earlier")
        rows = []
        for pi, a in enumerate(planned):
            if a.get("step") != name:
                continue
            ri = plan_to_run.get(pi)
            if ri is not None:
                rows.append(run_row(ri, closed, True))
            else:
                rows.append({**_action_view(a), "planned": True, "tx_hash": None, "gas_used": None, "error": None,
                             "status": "earlier" if status == "earlier" else ("not-needed" if closed else "upcoming")})
        rows += [run_row(ri, closed, False) for ri in run_only if run_actions[ri].get("step") == name]
        steps.append({"name": name, "status": status, "actions": rows,
                      "earlier_hashes": earlier_steps.get(name, []),
                      "error": failed.get("error") if status == "failed" and failed else None})

    signable = [r for s in steps for r in s["actions"] if r["status"] not in ("not-needed", "skipped")]
    return {
        "steps": steps,
        "planKnown": bool(planned),
        "total": len(signable),
        "signed": sum(1 for r in signable if r["status"] in ("signed", "sent", "earlier")),
    }


# --- wallet hand-off ---------------------------------------------------------------

class SkipRequested(Exception):
    """The operator pressed Skip on the page for an optional transaction. Steps that mark an
    action `optional` catch it and record the action as skipped; for any other action it
    cannot happen (the server refuses a skip of a required transaction)."""

@dataclass
class PendingTx:
    id: int
    label: str
    tx: dict
    submitted_at: float
    action_index: int | None = None
    stage: str | None = None
    optional: bool = False
    skipped: bool = False
    hash: str | None = None
    hashes: list[str] = field(default_factory=list)
    batch: list[dict] | None = None
    subcalls: list[str] | None = None      # display only: the calls a packed transaction carries
    error: str | None = None
    done: threading.Event = field(default_factory=threading.Event)


def fork_marker_matches(marker: dict, seen: str | int | None) -> bool:
    if seen is None:
        return False
    try:
        return (int(seen, 16) if isinstance(seen, str) else int(seen)) == int(marker["balance"], 16)
    except (TypeError, ValueError):
        return False


def wallet_not_on_fork_problem() -> str:
    return ("the wallet is not connected to the fork: its RPC does not see the fork's marker account, so a "
            "signature would go to the LIVE chain. Point the wallet's network RPC at the fork URL shown on the page")


class SigningQueue:
    """Thread-safe hand-off between the pipeline thread and the page."""

    def __init__(self, chain_id: int, expected_signer: str | None):
        self.chain_id = int(chain_id)
        self.expected_signer = Web3.to_checksum_address(expected_signer) if expected_signer else None
        self._lock = threading.Lock()
        self._next = 1
        self._pending: PendingTx | None = None
        self._history: list[PendingTx] = []
        self.account: str | None = None
        self.wallet_chain: int | None = None
        self._connected = threading.Event()
        # On a local fork: an address the pipeline funded with a random balance on the fork only.
        # A fork has the live chain's id, so the chain id cannot tell a wallet on the fork from one
        # on the live chain; the marker balance read through the wallet can.
        self.fork_marker: dict | None = None
        self.atomic = False     # the wallet reported atomic batch support (EIP-5792) on this chain

    # --- page side -----------------------------------------------------------------
    def connect(self, account: str, chain_id: int, marker_balance: str | int | None = None, atomic: bool = False) -> str | None:
        """Record the wallet's account/chain. Returns a problem text, or None when usable.
        `marker_balance` is the fork marker's balance as the WALLET's RPC reports it."""
        acct = Web3.to_checksum_address(account)
        problem = None
        if int(chain_id) != self.chain_id:
            problem = f"wallet is on chain {int(chain_id)}, the strategy targets {self.chain_id}"
        elif self.expected_signer and acct != self.expected_signer:
            problem = f"wallet account {acct} is not the expected deployer {self.expected_signer}"
        elif self.fork_marker and not fork_marker_matches(self.fork_marker, marker_balance):
            problem = wallet_not_on_fork_problem()
        with self._lock:
            self.account, self.wallet_chain, self.atomic = acct, int(chain_id), bool(atomic)
            if problem is None:
                self._connected.set()
            else:
                self._connected.clear()
        return problem

    def state(self) -> dict:
        with self._lock:
            p = self._pending
            return {
                "chainId": self.chain_id,
                "expectedSigner": self.expected_signer,
                "account": self.account,
                "connected": self._connected.is_set(),
                "pending": None if p is None or p.done.is_set()
                else {"id": p.id, "label": p.label, "tx": p.tx, "actionIndex": p.action_index, "stage": p.stage, "optional": p.optional,
                      "batch": p.batch, "subcalls": p.subcalls},
                "history": [{"id": h.id, "label": h.label, "hash": h.hash, "error": h.error} for h in self._history[-50:]],
            }

    def sends(self, stage: str | None = None) -> dict[int, dict]:
        """What the wallet did per recorder action index of one stage (the latest attempt wins)."""
        with self._lock:
            return {h.action_index: {"hash": h.hash, "error": None if h.skipped else h.error, "skipped": h.skipped,
                                     "pending": not h.done.is_set()}
                    for h in self._history if h.action_index is not None and h.stage == stage}

    def retarget(self, chain_id: int, expected_signer: str | None) -> None:
        """Point the queue at the next vault's chain and deployer. A wallet on another chain or
        account is disconnected until it switches, and the page asks for the switch."""
        with self._lock:
            self.chain_id = int(chain_id)
            self.expected_signer = Web3.to_checksum_address(expected_signer) if expected_signer else None
            ok = (self.account is not None and self.wallet_chain == self.chain_id
                  and (self.expected_signer is None or self.account == self.expected_signer))
            if not ok:
                self._connected.clear()

    def complete(self, tx_id: int, tx_hash: str, hashes: list[str] | None = None) -> bool:
        with self._lock:
            p = self._pending
            if p is None or p.id != int(tx_id) or p.done.is_set():
                return False
            p.hash = tx_hash
            p.hashes = [str(h) for h in (hashes or [tx_hash])]
            p.done.set()
            return True

    def skip(self, tx_id: int) -> bool:
        """Skip an optional transaction. A required one cannot be skipped from the page."""
        with self._lock:
            p = self._pending
            if p is None or p.id != int(tx_id) or p.done.is_set() or not p.optional:
                return False
            p.skipped, p.error = True, "skipped by the operator"
            p.done.set()
            return True

    def fail(self, tx_id: int, error: str) -> bool:
        with self._lock:
            p = self._pending
            if p is None or p.id != int(tx_id) or p.done.is_set():
                return False
            p.error = error or "rejected in the wallet"
            p.done.set()
            return True

    # --- pipeline side -------------------------------------------------------------
    def wait_for_connection(self, timeout_s: float | None = None) -> bool:
        return self._connected.wait(timeout_s)

    def submit(self, tx: dict, label: str, action_index: int | None = None, stage: str | None = None,
               optional: bool = False) -> PendingTx:
        with self._lock:
            if self._pending is not None and not self._pending.done.is_set():
                raise RuntimeError("a transaction is already waiting for a signature")
            p = PendingTx(id=self._next, label=label, tx=tx, submitted_at=time.time(), action_index=action_index, stage=stage, optional=optional)
            self._next += 1
            self._pending = p
            self._history.append(p)
            return p

    def submit_batch(self, calls: list[dict], label: str, stage: str | None = None) -> PendingTx:
        with self._lock:
            if self._pending is not None and not self._pending.done.is_set():
                raise RuntimeError("a transaction is already waiting for a signature")
            p = PendingTx(id=self._next, label=label, tx={"to": None, "data": "0x", "value": "0x0"}, submitted_at=time.time(),
                          stage=stage, batch=[{k: c.get(k) for k in ("to", "data", "value", "label", "gas_used")} for c in calls])
            self._next += 1
            self._pending = p
            self._history.append(p)
            return p

    def wait(self, p: PendingTx, timeout_s: float) -> str:
        if not p.done.wait(timeout_s):
            with self._lock:
                p.error = f"no signature within {int(timeout_s)} s"
                p.done.set()
            raise TimeoutError(p.error)
        if p.skipped:
            raise SkipRequested(p.label)
        if p.error:
            raise RuntimeError(f"wallet did not sign '{p.label}': {p.error}")
        assert p.hash
        return p.hash


class BrowserSigner:
    """Local page + `Web3Context.send` override + step lifecycle. Start, wait for the wallet, install."""

    def __init__(self, queue: SigningQueue, port: int = 8789, host: str = "127.0.0.1",
                 sign_timeout_s: float = 3600.0, page: Path = PAGE, log=print, meta: dict | None = None):
        self.queue, self.port, self.host, self.sign_timeout_s, self.page, self.log = queue, port, host, sign_timeout_s, page, log
        self.meta: dict = dict(meta or {})          # vault, chain, factory, plan, step names: see cli._signer_meta
        self.token = secrets.token_urlsafe(24)     # served inline in the page; a POST without it is refused
        self._server: ThreadingHTTPServer | None = None
        self._recorder = None
        self._ctx = None
        self._instance_source: Callable[[], dict | None] = lambda: None
        self._lock = threading.Lock()
        self._current: str | None = None
        self._finished: set[str] = set()
        self._failed: dict | None = None
        self._outcome: dict | None = None           # set by finish(); the page switches to the result view
        self._closed = threading.Event()
        self.disclaimer_accepted_at: str | None = None
        self.stage_id: str | None = None             # campaign stage being signed (None for a single vault)
        self._archive: dict[str, dict] = {}          # finished stages' boards, so the page can reopen them
        self.campaign_source: Callable[[], dict | None] = lambda: None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    # --- lifecycle hooks (pipeline thread) -------------------------------------------
    def step_started(self, name: str) -> None:
        with self._lock:
            self._current = name

    def step_finished(self, name: str) -> None:
        with self._lock:
            self._finished.add(name)
            if self._current == name:
                self._current = None

    def step_failed(self, name: str, error: str) -> None:
        with self._lock:
            self._failed = {"step": name, "error": error}
            self._current = None

    def finish(self, outcome: dict) -> None:
        with self._lock:
            self._outcome = outcome

    def begin_stage(self, meta: dict, stage_id: str | None = None) -> None:
        """Start the next vault (or connection) of a campaign on the same page: the finished
        stage's board is archived for review, and the step tracking starts over."""
        with self._lock:
            prev = self.stage_id
        if prev is not None and prev != (stage_id or meta.get("stage_id")):
            self._archive[prev] = self.board()
        with self._lock:
            keep = {k: self.meta[k] for k in ("is_local_fork", "fork_rpc", "fork_marker") if k in self.meta}
            self.meta = {**keep, **meta}
            self.stage_id = stage_id or meta.get("stage_id")
            self._current, self._finished, self._failed, self._outcome = None, set(), None, None
            self._recorder = None

    def archive_current(self) -> None:
        if self.stage_id is not None:
            self._archive[self.stage_id] = self.board()

    def board(self) -> dict:
        raw = list(self._recorder.actions) if self._recorder is not None else []
        actions = [asdict(a) if is_dataclass(a) else dict(a) for a in raw]
        with self._lock:
            current, finished, failed, outcome = self._current, set(self._finished), self._failed, self._outcome
        b = build_board(self.meta.get("steps", []), self.meta.get("planned"), actions, self.queue.sends(self.stage_id),
                        current=current, failed=failed, finished_steps=finished,
                        earlier_steps=self.meta.get("earlier_steps", {}))
        b.update({
            "vault": self.meta.get("vault"), "chain": self.meta.get("chain"), "factory": self.meta.get("factory"),
            "feePackageIndex": self.meta.get("fee_package_index"), "planNote": self.meta.get("plan_note"),
            "isLocalFork": self.meta.get("is_local_fork", False), "forkRpc": self.meta.get("fork_rpc"),
            "forkMarker": self.meta.get("fork_marker"),
            "strategy": self.meta.get("strategy"),
            "instance": self._instance_source() or None, "outcome": outcome, "current": current,
            "stageId": self.stage_id, "kind": self.meta.get("kind", "vault"),
        })
        return b

    # --- server --------------------------------------------------------------------
    def start(self) -> None:
        q, page, signer = self.queue, self.page, self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):   # keep the pipeline output clean
                pass

            def _json(self, code: int, obj: Any) -> None:
                body = json.dumps(obj, default=str).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _local(self) -> bool:
                # DNS-rebinding guard: answer only requests addressed to a loopback name
                host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
                return host in LOCAL_HOSTS

            def do_GET(self):
                if not self._local():
                    return self._json(403, {"error": "local access only"})
                if self.path.startswith("/api/state"):
                    return self._json(200, {**q.state(), "board": signer.board(),
                                            "campaign": signer.campaign_source(), "archive": signer._archive})
                if self.path in ("/", "/index.html"):
                    body = page.read_text(encoding="utf-8").replace(TOKEN_PLACEHOLDER, signer.token).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self._json(404, {"error": "not found"})

            def do_POST(self):
                if not self._local() or self.headers.get("X-Signer-Token") != signer.token:
                    return self._json(403, {"error": "refused: open the page from the URL the pipeline printed"})
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except json.JSONDecodeError:
                    return self._json(400, {"error": "bad json"})
                if self.path == "/api/connect":
                    # the operator must accept the page's as-is disclaimer before any wallet is used
                    if body.get("accepted") is not True:
                        return self._json(200, {"ok": False, "problem": "accept the disclaimer on the page first"})
                    if signer.disclaimer_accepted_at is None:
                        signer.disclaimer_accepted_at = time.strftime("%Y-%m-%d %H:%M:%S")
                        signer.log(f"[browser-signer] operator accepted the signing-page disclaimer at {signer.disclaimer_accepted_at}")
                    signer.log(f"[browser-signer] wallet {body.get('wallet')} capabilities: {json.dumps(body.get('caps'))[:600]}")
                    problem = q.connect(body["account"], int(body["chainId"], 16) if isinstance(body["chainId"], str) else int(body["chainId"]),
                                        body.get("markerBalance"), bool(body.get("atomic")))
                    return self._json(200, {"ok": problem is None, "problem": problem})
                if self.path == "/api/result":
                    if body.get("skip"):
                        ok = q.skip(body["id"])
                    elif body.get("hash"):
                        ok = q.complete(body["id"], body["hash"], body.get("hashes"))
                    else:
                        ok = q.fail(body["id"], str(body.get("error") or ""))
                    return self._json(200 if ok else 409, {"ok": ok})
                if self.path == "/api/close":
                    signer._closed.set()
                    return self._json(200, {"ok": True})
                self._json(404, {"error": "not found"})

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        threading.Thread(target=self._server.serve_forever, name="browser-signer", daemon=True).start()
        self.log(f"[browser-signer] open {self.url} and connect the wallet "
                 f"(chain {self.queue.chain_id}"
                 + (f", account {self.queue.expected_signer}" if self.queue.expected_signer else "") + ")")

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None

    def linger(self, timeout_s: float) -> None:
        """Keep the page up after the run so the record stays browsable. Returns when the
        operator presses 'Close signing session' on the page, on Ctrl+C, or after `timeout_s`."""
        if timeout_s > 0:
            self.log(f"[browser-signer] run finished; the page stays at {self.url} for up to {int(timeout_s // 60)} min "
                     "(press 'Close signing session' on the page or Ctrl+C to stop)")
            try:
                self._closed.wait(timeout_s)
            except KeyboardInterrupt:
                pass
        self.stop()

    def wait_for_wallet(self, timeout_s: float = 3600.0) -> str:
        if not self.queue.wait_for_connection(timeout_s):
            raise TimeoutError(f"no wallet connected to {self.url} within {int(timeout_s)} s")
        assert self.queue.account
        self.log(f"[browser-signer] wallet connected: {self.queue.account} on chain {self.queue.wallet_chain}")
        return self.queue.account

    def send_packed(self, tx: dict, label: str) -> str:
        """One ordinary transaction that carries several simulated calls (an AccessManager multicall);
        the page lists them. Returns the transaction hash."""
        built = self._ctx.build_transaction(tx["to"], bytes.fromhex(tx["data"][2:])) if self._ctx else tx
        pending = self.queue.submit(sanitize_for_wallet(built), label, stage=self.stage_id)
        pending.subcalls = [c.get("label") or c.get("to") for c in tx.get("calls", [])]
        self.log(f"[browser-signer] waiting for signature #{pending.id}: {label}")
        return self.queue.wait(pending, self.sign_timeout_s)

    def send_batch(self, calls: list[dict], label: str) -> list[str]:
        """Hand a list of calls to the wallet as one atomic batch; returns the transaction hashes."""
        pending = self.queue.submit_batch(calls, label, stage=self.stage_id)
        self.log(f"[browser-signer] waiting for one confirmation of {len(calls)} calls: {label}")
        self.queue.wait(pending, self.sign_timeout_s)
        return pending.hashes

    def install(self, ctx, recorder=None, label_source=None, instance_source=None) -> None:
        """Point the SDK context at the wallet account and route `send` through the page.
        `recorder` links each wallet prompt to the action the step registered just before it."""
        self._recorder = recorder
        if instance_source:
            self._instance_source = instance_source
        ctx._signer = Web3.to_checksum_address(self.queue.account)   # SDK keeps the address private; no key is set
        self._ctx = ctx
        signer = self

        def browser_send(to, data):
            built = ctx.build_transaction(to, data)          # gas estimate + revert check, as with a key
            label = label_source() if label_source else f"{to}"
            idx = len(recorder.actions) - 1 if recorder is not None and recorder.actions else None
            optional = bool(idx is not None and getattr(recorder.actions[idx], "optional", False))
            pending = signer.queue.submit(sanitize_for_wallet(built), label, action_index=idx, stage=signer.stage_id, optional=optional)
            signer.log(f"[browser-signer] waiting for signature #{pending.id}: {label}")
            tx_hash = signer.queue.wait(pending, signer.sign_timeout_s)
            receipt = ctx.web3.eth.wait_for_transaction_receipt(tx_hash, timeout=600)
            return ctx._handle_receipt(bytes.fromhex(tx_hash[2:]) if isinstance(tx_hash, str) else tx_hash, receipt)

        ctx.send = browser_send
