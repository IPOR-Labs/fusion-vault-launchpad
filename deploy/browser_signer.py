"""Sign pipeline transactions with a browser wallet instead of a private key.

`python -m deploy <spec> --broadcast --signer browser` starts a small local web
page (default http://127.0.0.1:8787). The pipeline builds every transaction
exactly as it would for a key (`Web3Context.build_transaction`: gas estimate,
revert check), hands the unsigned transaction to the page, and the operator
confirms it in MetaMask / Rabby / any injected wallet. The page returns the
transaction hash, the pipeline waits for the receipt and continues: same steps,
same state file, same verification report. One transaction at a time, in order.

Split: `SigningQueue` and `sanitize_for_wallet` are pure and unit-tested;
`BrowserSigner` owns the HTTP server thread and the `Web3Context.send` override.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from web3 import Web3

PAGE = Path(__file__).resolve().parent.parent / "tools" / "browser_signer" / "index.html"
WALLET_FIELDS = ("from", "to", "data", "value", "gas", "chainId")


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


@dataclass
class PendingTx:
    id: int
    label: str
    tx: dict
    submitted_at: float
    hash: str | None = None
    error: str | None = None
    done: threading.Event = field(default_factory=threading.Event)


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

    # --- page side -----------------------------------------------------------------
    def connect(self, account: str, chain_id: int) -> str | None:
        """Record the wallet's account/chain. Returns a problem text, or None when usable."""
        acct = Web3.to_checksum_address(account)
        problem = None
        if int(chain_id) != self.chain_id:
            problem = f"wallet is on chain {int(chain_id)}, the strategy targets {self.chain_id}"
        elif self.expected_signer and acct != self.expected_signer:
            problem = f"wallet account {acct} is not the expected deployer {self.expected_signer}"
        with self._lock:
            self.account, self.wallet_chain = acct, int(chain_id)
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
                "pending": None if p is None or p.done.is_set() else {"id": p.id, "label": p.label, "tx": p.tx},
                "history": [{"id": h.id, "label": h.label, "hash": h.hash, "error": h.error} for h in self._history[-50:]],
            }

    def complete(self, tx_id: int, tx_hash: str) -> bool:
        with self._lock:
            p = self._pending
            if p is None or p.id != int(tx_id) or p.done.is_set():
                return False
            p.hash = tx_hash
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

    def submit(self, tx: dict, label: str) -> PendingTx:
        with self._lock:
            if self._pending is not None and not self._pending.done.is_set():
                raise RuntimeError("a transaction is already waiting for a signature")
            p = PendingTx(id=self._next, label=label, tx=tx, submitted_at=time.time())
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
        if p.error:
            raise RuntimeError(f"wallet did not sign '{p.label}': {p.error}")
        assert p.hash
        return p.hash


class BrowserSigner:
    """Local page + `Web3Context.send` override. Start, wait for the wallet, install."""

    def __init__(self, queue: SigningQueue, port: int = 8787, host: str = "127.0.0.1",
                 sign_timeout_s: float = 900.0, page: Path = PAGE, log=print):
        self.queue, self.port, self.host, self.sign_timeout_s, self.page, self.log = queue, port, host, sign_timeout_s, page, log
        self._server: ThreadingHTTPServer | None = None
        self._label_source = None   # callable returning the current step/action label

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def start(self) -> None:
        q, page = self.queue, self.page

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):   # keep the pipeline output clean
                pass

            def _json(self, code: int, obj: Any) -> None:
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.startswith("/api/state"):
                    return self._json(200, q.state())
                if self.path in ("/", "/index.html"):
                    body = page.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self._json(404, {"error": "not found"})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except json.JSONDecodeError:
                    return self._json(400, {"error": "bad json"})
                if self.path == "/api/connect":
                    problem = q.connect(body["account"], int(body["chainId"], 16) if isinstance(body["chainId"], str) else int(body["chainId"]))
                    return self._json(200, {"ok": problem is None, "problem": problem})
                if self.path == "/api/result":
                    if body.get("hash"):
                        ok = q.complete(body["id"], body["hash"])
                    else:
                        ok = q.fail(body["id"], str(body.get("error") or ""))
                    return self._json(200 if ok else 409, {"ok": ok})
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

    def wait_for_wallet(self, timeout_s: float = 900.0) -> str:
        if not self.queue.wait_for_connection(timeout_s):
            raise TimeoutError(f"no wallet connected to {self.url} within {int(timeout_s)} s")
        assert self.queue.account
        self.log(f"[browser-signer] wallet connected: {self.queue.account} on chain {self.queue.wallet_chain}")
        return self.queue.account

    def install(self, ctx, label_source=None) -> None:
        """Point the SDK context at the wallet account and route `send` through the page."""
        self._label_source = label_source
        ctx._signer = Web3.to_checksum_address(self.queue.account)   # SDK keeps the address private; no key is set
        signer = self

        def browser_send(to, data):
            built = ctx.build_transaction(to, data)          # gas estimate + revert check, as with a key
            label = signer._label_source() if signer._label_source else f"{to}"
            pending = signer.queue.submit(sanitize_for_wallet(built), label)
            signer.log(f"[browser-signer] waiting for signature #{pending.id}: {label}")
            tx_hash = signer.queue.wait(pending, signer.sign_timeout_s)
            receipt = ctx.web3.eth.wait_for_transaction_receipt(tx_hash)
            return ctx._handle_receipt(bytes.fromhex(tx_hash[2:]) if isinstance(tx_hash, str) else tx_hash, receipt)

        ctx.send = browser_send
