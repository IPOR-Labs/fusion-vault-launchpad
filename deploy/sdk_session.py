"""Web3 + SDK session wiring.

RPC URL resolution:
  1. RPC_URL env var — always wins. Required for --broadcast.
  2. The chain context's `public_rpc` — dry-runs only, so a first run works with
     no configuration at all. Public nodes rate-limit and may stall; never used
     to broadcast.

Private key: DEPLOYER_PRIVATE_KEY (required only with --broadcast). Without it
the pipeline can still validate a strategy, preview the clone addresses and
print the complete plan; it cannot create a vault.

A `.env` file at the repo root is loaded automatically (via python-dotenv).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from eth_typing import ChecksumAddress
from web3 import Web3

try:
    from dotenv import load_dotenv
    _ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
    if _ENV_PATH.exists():
        load_dotenv(_ENV_PATH)
except ImportError:
    pass  # dotenv optional — env vars still work via the shell

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.fusion_factory import FusionFactory

from deploy.config import StrategyConfig
from deploy.context import DeployContext
from deploy.guards import is_local_node
from deploy.plan import RunRecorder
from deploy.state import hash_config


def _install_monotonic_nonce(ctx) -> None:
    """Make the signer's nonce monotonic across txs.

    The SDK's `_build_transaction` calls `get_transaction_count(signer)` fresh per
    tx with no local increment. On a load-balanced public RPC, consecutive calls
    can hit backends with inconsistent views → two txs get the same nonce
    ('nonce too low' / 'replacement transaction underpriced'). We cache the next
    nonce locally and only ever advance it, resyncing upward if the chain is ahead.
    Anvil (instant mining) never exposed this; real chains do.
    """
    # SDK <= 3.1 names the builder `_build_transaction`; >= 3.6 exposes it as
    # `build_transaction`. `send()` looks the method up on the instance at call
    # time, so shadowing whichever name exists is enough.
    attr = "build_transaction" if hasattr(ctx, "build_transaction") else "_build_transaction"
    orig_build = getattr(ctx, attr)  # bound method
    state = {"next": None}

    def patched_build(to, data):
        tx = orig_build(to, data)
        n = tx["nonce"]
        if state["next"] is not None and state["next"] > n:
            n = state["next"]
        tx["nonce"] = n
        state["next"] = n + 1
        return tx

    setattr(ctx, attr, patched_build)


@dataclass(slots=True)
class Session:
    ctx: Web3Context
    factory: FusionFactory
    signer: ChecksumAddress
    rpc_url: str
    rpc_source: str
    broadcast: bool
    recorder: RunRecorder
    client_version: str
    is_local_node: bool


def resolve_rpc_url(context: DeployContext, broadcast: bool) -> tuple[str, str]:
    """Returns (rpc_url, source_label). Raises when a broadcast has no explicit RPC."""
    if rpc := os.environ.get("RPC_URL"):
        return rpc, "RPC_URL env"
    if broadcast:
        raise RuntimeError(
            "RPC_URL not set — a --broadcast needs an explicit endpoint: a local anvil fork "
            "(http://127.0.0.1:8546) for a rehearsal, or your own private RPC for a live chain."
        )
    if context.public_rpc:
        print(f"INFO: RPC_URL not set — using the context's public read-only endpoint "
              f"({context.public_rpc}) for this dry-run. Rate limits apply.", file=sys.stderr)
        return context.public_rpc, "context public_rpc"
    raise RuntimeError(f"RPC_URL not set and context '{context.name}' has no public_rpc.")


def open_session(cfg: StrategyConfig, context: DeployContext, broadcast: bool) -> Session:
    rpc, rpc_source = resolve_rpc_url(context, broadcast)
    pk = os.environ.get("DEPLOYER_PRIVATE_KEY") or None
    if broadcast and not pk:
        raise RuntimeError(
            "DEPLOYER_PRIVATE_KEY not set — required for --broadcast. Simulation (dry-run) works "
            "without it; creating a vault does not. Ask the human operator to add the key to .env "
            "(see .env.example). Never ask them to paste it into the chat."
        )
    if not broadcast and not pk:
        print("INFO: DEPLOYER_PRIVATE_KEY not set — dry-run only (no signer).", file=sys.stderr)

    gas_mult = float(cfg.raw["execution"].get("gas_multiplier", 1.25))
    ctx = Web3Context.from_url(rpc, private_key=pk, gas_multiplier=gas_mult)

    if not ctx.web3.is_connected():
        raise RuntimeError(f"RPC not reachable: {rpc} (source={rpc_source})")

    if broadcast and pk:
        _install_monotonic_nonce(ctx)

    try:
        client_version = str(ctx.web3.client_version)
    except Exception:
        client_version = ""
    local = is_local_node(client_version)

    actual_chain = int(ctx.web3.eth.chain_id)
    allowlist = cfg.raw["execution"].get("fork_chain_id_allowlist", [])
    # The live chain must be the configured chain, OR an explicitly acknowledged
    # alternative listed in fork_chain_id_allowlist (e.g. 31337 for a bare anvil).
    # On --broadcast any other chain is a HARD FAIL; a dry-run only warns.
    chain_acknowledged = actual_chain == cfg.chain_id or actual_chain in allowlist
    if broadcast and not chain_acknowledged:
        raise RuntimeError(
            f"chain_id mismatch on --broadcast: live RPC reports {actual_chain}, "
            f"strategy targets {cfg.chain_id}. If this is intentional (e.g. a bare anvil), "
            f"add {actual_chain} to execution.fork_chain_id_allowlist (current allowlist={allowlist})."
        )
    if not chain_acknowledged:
        print(f"WARNING: chain_id mismatch (RPC={actual_chain}, cfg={cfg.chain_id})")

    factory = FusionFactory(ctx, context.fusion_factory)
    signer = ctx.signer or Web3.to_checksum_address("0x" + "00" * 20)
    print(f"INFO: RPC source={rpc_source} chain_id={actual_chain} node={'local fork' if local else 'live'} "
          f"signer={signer}", file=sys.stderr)
    recorder = RunRecorder(
        strategy=cfg.name,
        config_hash=hash_config(cfg.raw),
        mode="broadcast" if broadcast else "dry-run",
        chain_id=actual_chain,
        rpc_source=rpc_source,
        signer=str(signer),
        gas_multiplier=gas_mult,
        context=context.name,
    )
    return Session(
        ctx=ctx,
        factory=factory,
        signer=signer,
        rpc_url=rpc,
        rpc_source=rpc_source,
        broadcast=broadcast,
        recorder=recorder,
        client_version=client_version,
        is_local_node=local,
    )
