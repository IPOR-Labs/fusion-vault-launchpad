"""Few signatures per vault: simulate the whole deployment on a throwaway fork, then send the recorded
calls packed into the vault's own AccessManager `multicall`.

Every setup call is role-restricted. IPOR's AccessManager (OpenZeppelin) has `multicall(bytes[])` and
`execute(target, data)`: the deployer calls `multicall` once, each entry is a role call on the
AccessManager or `execute(vault / withdraw / price / fee / rewards / context manager, call)`, and the
AccessManager checks the deployer's role for every entry before relaying it. No extra contract, no
Safe, no EIP-7702, and nobody receives a permission the spec does not grant. A multicall is atomic.
Calls to contracts outside the vault (the factory clone, price-feed factories, a child's
AccessManager) stay separate transactions, in their original order.

Flow (`deploy/cli.py`, `--batch` with `--signer browser`, when the wallet reports atomic support):
  1. `simulate`: start anvil on the live RPC, run this strategy there with `--signer impersonate`
     as the deployer, capture every call in order (clone, roles, fuses, substrates, balance fuses,
     dependency graph, price feeds, withdraw manager, fees, whitelist, final roles, and the
     WHITELIST grants that let this vault deposit into its children).
  2. `chunks`: split by gas so each batch fits a block (Arbitrum 32 M; HyperEVM big block 30 M).
  3. `access_manager_txs`: pack runs of vault-managed calls into AccessManager.multicall transactions.
  4. `adopt`: write the live state from the simulation, with the live hashes, then the normal
     verification reads the live chain.

Before the first chunk the clone preview is re-read on the live chain: if the factory moved since the
simulation, the addresses would differ and the batch is refused (re-run to simulate again).
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
HYPEREVM_CHAIN = 999
HYPEREVM_BIG_BLOCK_GAS = 30_000_000
BLOCK_FILL = 0.8          # keep each batch well under the block gas limit (7702 overhead, estimate drift)


@dataclass
class Simulation:
    calls: list[dict] = field(default_factory=list)       # {to, data, value, gas_used, label}
    authority: dict[str, str] = field(default_factory=dict)  # target (lower) -> its AccessManager, read on the fork
    state: dict[str, Any] = field(default_factory=dict)   # the fork run's state file
    actions: list[dict] = field(default_factory=list)     # the fork run's recorder actions
    log: str = ""


def anvil_binary() -> str:
    for c in (os.environ.get("ANVIL"), shutil.which("anvil"), str(Path.home() / ".foundry/bin/anvil")):
        if c and Path(c).exists():
            return c
    raise RuntimeError("--batch needs anvil (Foundry) to simulate the deployment; install Foundry or set ANVIL")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_rpc(url: str, timeout_s: float = 60) -> None:
    import urllib.request
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}).encode()
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            urllib.request.urlopen(urllib.request.Request(url, body, {"content-type": "application/json"}), timeout=3).read()
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"simulation fork at {url} did not start")


def simulate(strategy: Path, live_rpc: str, deployer: str, link_grants: list[str], log=print) -> Simulation:
    """Run the whole deployment of `strategy` on a fresh fork of `live_rpc` as `deployer`."""
    tmp = Path(tempfile.mkdtemp(prefix="fusion-batch-"))
    port = _free_port()
    fork = f"http://127.0.0.1:{port}"
    anvil = subprocess.Popen([anvil_binary(), "--fork-url", live_rpc, "--port", str(port), "--auto-impersonate",
                              "--gas-limit", "100000000", "--silent"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait_rpc(fork)
        env = {**os.environ, "RPC_URL": fork, "DEPLOYER_ADDRESS": deployer,
               "DEPLOY_STATE_PATH": str(tmp / "state.json"), "DEPLOY_CAPTURE": str(tmp / "calls.jsonl"),
               "DEPLOY_LINK_GRANTS": json.dumps(link_grants)}
        env.pop("DEPLOYER_PRIVATE_KEY", None)
        log(f"[batch] simulating {strategy.name} on a fork of the live chain ({fork}) …")
        proc = subprocess.run([sys.executable, "-m", "deploy", str(strategy), "--broadcast", "--signer", "impersonate",
                               "--force-restart", "--signer-linger", "0"], cwd=ROOT, env=env, capture_output=True, text=True)
        sim = Simulation(log=proc.stdout + proc.stderr)
        if proc.returncode != 0:
            tail = "\n".join(sim.log.strip().splitlines()[-15:])
            raise RuntimeError(f"the simulation on the fork failed; nothing was sent:\n{tail}")
        sim.calls = [json.loads(l) for l in (tmp / "calls.jsonl").read_text().splitlines() if l.strip()]
        sim.state = json.loads((tmp / "state.json").read_text())
        for to in {c["to"] for c in sim.calls}:
            a = _authority(fork, to)
            if a:
                sim.authority[to.lower()] = a
        run = tmp / "state.run.json"
        sim.actions = json.loads(run.read_text()).get("actions", []) if run.exists() else []
        log(f"[batch] simulation OK: {len(sim.calls)} calls, {sum(c['gas_used'] for c in sim.calls):,} gas")
        return sim
    finally:
        anvil.terminate()
        try:
            anvil.wait(10)
        except subprocess.TimeoutExpired:
            anvil.kill()
        shutil.rmtree(tmp, ignore_errors=True)


MAX_TX_GAS = 30_000_000   # Arbitrum caps one transaction at 32 M while reporting a ~1e15 block gas limit


def _authority(rpc: str, target: str) -> str | None:
    """`authority()` of an AccessManaged contract, or None."""
    import urllib.request
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                       "params": [{"to": target, "data": "0xbf7e214f"}, "latest"]}).encode()
    try:
        res = json.loads(urllib.request.urlopen(urllib.request.Request(rpc, body, {"content-type": "application/json"}), timeout=10).read())
        r = res.get("result") or ""
        return "0x" + r[-40:] if len(r) >= 66 and int(r, 16) else None
    except Exception:
        return None


def batch_gas_limit(chain_id: int, latest_block_gas_limit: int) -> int:
    """Gas one batch transaction may use. Never trust a huge reported block limit (Arbitrum)."""
    if chain_id == HYPEREVM_CHAIN:
        return HYPEREVM_BIG_BLOCK_GAS
    return min(int(latest_block_gas_limit), MAX_TX_GAS)


def chunks(calls: list[dict], gas_limit: int) -> list[list[dict]]:
    """Split the calls, in order, into batches that each stay under `gas_limit * BLOCK_FILL`."""
    cap = int(gas_limit * BLOCK_FILL)
    out: list[list[dict]] = [[]]
    used = 0
    for c in calls:
        g = int(c.get("gas_used") or 0)
        if g > cap:
            raise RuntimeError(f"one call ({c.get('label')}) uses {g:,} gas, above {cap:,} for one block; it cannot be batched")
        if out[-1] and used + g > cap:
            out.append([])
            used = 0
        out[-1].append(c)
        used += g
    return [c for c in out if c]


def adopt(sim: Simulation, hashes_per_call: list[str]) -> dict:
    """The live state file content: the simulation's state with every step's hashes replaced by the
    live hash of the batch that carried its calls."""
    by_label: dict[str, list[str]] = {}
    for c, h in zip(sim.calls, hashes_per_call):
        by_label.setdefault(c.get("step") or "", []).append(h)
    state = json.loads(json.dumps(sim.state))
    for rec in state.get("completed_steps", []):
        hs = by_label.get(rec["step"])
        rec["tx_hashes"] = sorted(set(hs), key=hs.index) if hs else []
    return state


MULTICALL = "ac9650d8"      # multicall(bytes[])
EXECUTE = "1cff79cd"        # execute(address,bytes)


def _hexbytes(h: str) -> bytes:
    return bytes.fromhex(h[2:] if h.startswith("0x") else h)


def access_manager_txs(calls: list[dict], access_manager: str, managed: set[str], gas_limit: int) -> list[dict]:
    """Turn the simulated calls into transactions. Consecutive calls to the AccessManager or to a
    contract it manages become one `multicall` (split by gas); any other call is its own transaction.
    Each transaction lists the simulated calls it carries (`calls`)."""
    from eth_abi import encode
    am = access_manager.lower()
    managed = {a.lower() for a in managed}
    cap = int(gas_limit * BLOCK_FILL)
    txs: list[dict] = []
    group: list[dict] = []

    def flush():
        if not group:
            return
        items = []
        for c in group:
            data = _hexbytes(c["data"])
            items.append(data if c["to"].lower() == am else bytes.fromhex(EXECUTE) + encode(["address", "bytes"], [c["to"], data]))
        txs.append({"to": access_manager, "data": "0x" + MULTICALL + encode(["bytes[]"], [items]).hex(), "value": "0x0",
                    "gas_used": sum(int(c.get("gas_used") or 0) for c in group), "calls": list(group),
                    "label": f"AccessManager.multicall · {len(group)} calls"})
        group.clear()

    for c in calls:
        to = c["to"].lower()
        if to == am or to in managed:
            if group and sum(int(g.get("gas_used") or 0) for g in group) + int(c.get("gas_used") or 0) > cap:
                flush()
            group.append(c)
        else:
            flush()
            txs.append({**c, "calls": [c]})
    flush()
    return txs
