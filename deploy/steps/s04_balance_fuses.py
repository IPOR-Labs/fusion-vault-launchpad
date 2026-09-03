"""Step 04: add balance fuses + set dependency graph edges."""
from __future__ import annotations

from eth_abi import encode as abi_encode, decode as abi_decode
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.core.plasma_vault import PlasmaVault

from deploy.graph import derive_dependency_graph, graph_to_calldata_args

NAME = "04_balance_fuses"


def _selector(sig: str) -> bytes:
    return function_signature_to_4byte_selector(sig)


def _balance_fuse_supported(session, vault_addr, market_id, fuse_addr) -> bool:
    """Idempotency guard: a re-run/resume must not re-add an existing balance fuse
    (the contract reverts 0x7a056553). Read isBalanceFuseSupported first."""
    cd = _selector("isBalanceFuseSupported(uint256,address)") + abi_encode(
        ["uint256", "address"], [market_id, fuse_addr]
    )
    try:
        return abi_decode(["bool"], bytes(session.ctx.call(vault_addr, cd)))[0]
    except Exception:
        return False


def run(cfg, deploy_ctx, session, instance, state, broadcast):
    if state.has_step(NAME):
        print(f"[{NAME}] already done — skipping")
        return
    vault_addr = instance["plasma_vault"]
    vault = PlasmaVault(session.ctx, vault_addr)
    tx_hashes = []

    for bf in cfg.raw["balance_fuses"]:
        market_id = deploy_ctx.market_id(bf["market"])
        fuse_addr = deploy_ctx.fuse(bf["fuse"])
        rec = session.recorder.add(
            NAME, action="addBalanceFuse", key=bf["market"],
            target=vault_addr, function="addBalanceFuse(uint256,address)",
            args={"market": bf["market"], "market_id": market_id, "fuse": bf["fuse"]},
        )
        if _balance_fuse_supported(session, vault_addr, market_id, fuse_addr):
            print(f"[{NAME}] balance fuse market={bf['market']} ({market_id}) already supported — skip")
            rec.skipped = True
            rec.note = "already supported on-chain"
            continue
        print(f"[{NAME}] add_balance_fuse market={bf['market']} ({market_id}) -> {bf['fuse']} {fuse_addr}")
        if broadcast:
            receipt = vault.add_balance_fuse(market_id, fuse_addr).send()
            tx_hash = receipt["transactionHash"].hex()
            tx_hashes.append(tx_hash)
            rec.executed = True
            rec.tx_hash = tx_hash
            rec.gas_used = int(receipt["gasUsed"])

    # Dependency balance graph via updateDependencyBalanceGraphs(uint256[], uint256[][]).
    # AUTO-DERIVE the complete graph from deploy.graph (the single source of truth the
    # verifier also consumes) — do NOT trust the JSON's dependency_graph alone, which
    # has been authored incomplete: a missing lending edge silently corrupts NAV.
    derived = derive_dependency_graph(cfg, deploy_ctx)
    if derived:
        market_ids, deps = graph_to_calldata_args(derived)
        print(f"[{NAME}] dependency_graph (auto-derived) edges={len(market_ids)}: " +
              ", ".join(f"{m}->{deps[i]}" for i, m in enumerate(market_ids)))
        sig = "updateDependencyBalanceGraphs(uint256[],uint256[][])"
        calldata = _selector(sig) + abi_encode(["uint256[]", "uint256[][]"], [market_ids, deps])
        rec = session.recorder.add(
            NAME, action="updateDependencyBalanceGraphs", key="graph",
            target=vault_addr, function=sig,
            args={"edges": {str(m): deps[i] for i, m in enumerate(market_ids)}},
            calldata="0x" + calldata.hex(),
        )
        if broadcast:
            receipt = session.ctx.send(vault_addr, calldata)
            tx_hash = receipt["transactionHash"].hex()
            tx_hashes.append(tx_hash)
            rec.executed = True
            rec.tx_hash = tx_hash
            rec.gas_used = int(receipt["gasUsed"])
            print(f"[{NAME}]   tx {tx_hash} gas={receipt['gasUsed']}")

    if broadcast:
        state.record(NAME, tx_hashes=tx_hashes)
