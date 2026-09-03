"""deploy.graph — the dependency-graph derivation shared by s04 and verification."""
from types import SimpleNamespace

import pytest

from deploy.graph import derive_dependency_graph, graph_to_calldata_args


class _Ctx:
    """Stub deploy context exposing only market_id, like the real DeployContext."""
    _M = {"ERC20_VAULT_BALANCE": 7, "AAVE_V3": 1, "MORPHO": 14,
          "MORPHO_FLASH_LOAN": 19, "UNIVERSAL_TOKEN_SWAPPER": 12}

    def market_id(self, name):
        return self._M[name]


def _cfg(balance_fuses, dependency_graph=None):
    raw = {"balance_fuses": balance_fuses}
    if dependency_graph is not None:
        raw["dependency_graph"] = dependency_graph
    return SimpleNamespace(raw=raw)


def test_every_non_idle_market_depends_on_erc20():
    cfg = _cfg([
        {"market": "ERC20_VAULT_BALANCE", "fuse": "x"},
        {"market": "AAVE_V3", "fuse": "y"},
        {"market": "MORPHO", "fuse": "z"},
    ])
    g = derive_dependency_graph(cfg, _Ctx())
    assert g == {1: {7}, 14: {7}}  # ERC20 (7) itself has no self-edge


def test_explicit_edges_are_unioned():
    cfg = _cfg(
        [{"market": "AAVE_V3", "fuse": "y"}],
        dependency_graph=[{"from": "AAVE_V3", "to": ["MORPHO", "UNIVERSAL_TOKEN_SWAPPER"]}],
    )
    g = derive_dependency_graph(cfg, _Ctx())
    assert g == {1: {7, 14, 12}}


def test_empty_config_yields_empty_graph():
    assert derive_dependency_graph(_cfg([]), _Ctx()) == {}


def test_calldata_args_are_sorted_and_deterministic():
    g = {14: {7}, 1: {7}, 12: {7}, 19: {7}}
    mids, deps = graph_to_calldata_args(g)
    assert mids == [1, 12, 14, 19]
    assert deps == [[7], [7], [7], [7]]


def test_calldata_inner_lists_sorted():
    mids, deps = graph_to_calldata_args({1: {14, 7, 12}})
    assert mids == [1]
    assert deps == [[7, 12, 14]]
