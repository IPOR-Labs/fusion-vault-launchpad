"""Deterministic dependency-balance-graph derivation.

Single source of truth shared by the builder (`s04_balance_fuses`) and the
verifier (`verification.py`). Both MUST derive the expected graph identically,
or the verification pass is meaningless — so the logic lives here exactly once.

Rule (confirmed against the production Base cbBTC vault, where Aave / Morpho /
swap / flash-loan markets ALL depend on ERC20): every market that carries a
balance fuse EXCEPT the idle ERC20 balance itself moves the underlying through
that idle balance — supply/borrow consume and produce it; swaps and flash-loans
transit it — so each such market depends on ``ERC20_VAULT_BALANCE``. Explicit
edges declared in the JSON ``dependency_graph`` are then unioned on top. A
missing edge silently corrupts NAV, which is why we derive rather than trust the
JSON alone.
"""
from __future__ import annotations

# Market whose balance fuse tracks the vault's idle (uninvested) underlying.
ERC20_BALANCE_MARKET = "ERC20_VAULT_BALANCE"


def derive_dependency_graph(cfg, deploy_ctx) -> dict[int, set[int]]:
    """Return the complete expected ``{market_id: {dependency_market_id, ...}}``.

    Parameters
    ----------
    cfg
        Anything exposing ``.raw`` (a ``StrategyConfig`` or a plain holder in
        tests). Reads ``raw["balance_fuses"]`` and ``raw["dependency_graph"]``.
    deploy_ctx
        Anything exposing ``.market_id(name) -> int``.

    The result is order-independent and fully determined by the config — no
    chain reads — which is what makes it safe to assert against in tests and to
    diff between the builder and the verifier.
    """
    erc20_mid = deploy_ctx.market_id(ERC20_BALANCE_MARKET)
    derived: dict[int, set[int]] = {}

    for bf in cfg.raw.get("balance_fuses", []):
        mid = deploy_ctx.market_id(bf["market"])
        if mid != erc20_mid:
            derived.setdefault(mid, set()).add(erc20_mid)

    for edge in cfg.raw.get("dependency_graph", []):
        frm = deploy_ctx.market_id(edge["from"])
        for dep in edge["to"]:
            derived.setdefault(frm, set()).add(deploy_ctx.market_id(dep))

    return derived


def graph_to_calldata_args(derived: dict[int, set[int]]) -> tuple[list[int], list[list[int]]]:
    """Flatten a derived graph into the sorted ``(market_ids, deps)`` arg pair
    for ``updateDependencyBalanceGraphs(uint256[], uint256[][])``.

    Sorting both the outer market list and each inner dependency list makes the
    encoded calldata deterministic regardless of dict/set iteration order.
    """
    market_ids = sorted(derived)
    deps = [sorted(derived[m]) for m in market_ids]
    return market_ids, deps
