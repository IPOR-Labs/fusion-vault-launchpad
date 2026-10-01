"""deploy.campaign — pure parts: loading, order, blocking, stages, stand-in replacement."""
import json

import pytest

from deploy.campaign import blocked_reasons, deploy_order, load_campaign, resolve_strategy, stages

STAND_IN = "0x00000000000000000000000000000000000000c1"
CHILD = "0x00000000000000000000000000000000000000d2"


def _write(tmp_path, links, vaults=None):
    vaults = vaults or [
        {"id": "top", "strategy": "top.json"},
        {"id": "mid", "strategy": "mid.json"},
        {"id": "leaf", "strategy": "leaf.json"},
        {"id": "remote", "strategy": "remote.json", "blocked": "not deployable yet"},
    ]
    p = tmp_path / "set.campaign.json"
    p.write_text(json.dumps({"name": "set", "vaults": vaults, "links": links}))
    return p


LINKS = [
    {"parent": "top", "child": "mid", "kind": "erc4626", "placeholder": STAND_IN},
    {"parent": "mid", "child": "leaf", "kind": "erc4626", "placeholder": STAND_IN},
    {"parent": "mid", "child": "remote", "kind": "cross-chain", "blocked": "no fuse"},
]


def test_children_deploy_before_parents_and_connections_follow_their_parent(tmp_path):
    c = load_campaign(_write(tmp_path, LINKS))
    assert deploy_order(c) == ["leaf", "remote", "mid", "top"]
    ids = [s["id"] for s in stages(c)]
    assert ids == ["leaf", "remote", "mid", "link:mid<-leaf", "top", "link:top<-mid"]
    assert (tmp_path / "leaf.json").resolve() == c.vault("leaf").strategy


def test_a_cross_chain_link_is_drawn_but_never_blocks_nor_runs(tmp_path):
    c = load_campaign(_write(tmp_path, LINKS))
    assert blocked_reasons(c) == {"remote": "not deployable yet"}
    assert not any(s["id"].endswith("<-remote") for s in stages(c))


def test_a_parent_holding_a_blocked_child_is_blocked_too(tmp_path):
    links = [{"parent": "top", "child": "remote", "kind": "erc4626", "placeholder": STAND_IN}]
    c = load_campaign(_write(tmp_path, links))
    assert blocked_reasons(c)["top"] == "needs remote, which is blocked"
    assert next(s for s in stages(c) if s["id"] == "link:top<-remote")["blocked"]


def test_the_file_is_checked(tmp_path):
    with pytest.raises(ValueError, match="cycle"):
        load_campaign(_write(tmp_path, [
            {"parent": "top", "child": "mid", "kind": "erc4626", "placeholder": STAND_IN},
            {"parent": "mid", "child": "top", "kind": "erc4626", "placeholder": STAND_IN}]))
    with pytest.raises(ValueError, match="placeholder"):
        load_campaign(_write(tmp_path, [{"parent": "top", "child": "mid", "kind": "erc4626"}]))
    with pytest.raises(ValueError, match="unknown vault"):
        load_campaign(_write(tmp_path, [{"parent": "top", "child": "ghost", "kind": "erc4626", "placeholder": STAND_IN}]))


def test_stand_ins_are_replaced_everywhere_in_the_parent_only_once_the_child_exists(tmp_path):
    c = load_campaign(_write(tmp_path, LINKS))
    raw = {"substrates": [{"market": "ERC4626_0001", "values": [STAND_IN.upper().replace("0X", "0x")]}],
           "price_feeds": [{"asset": STAND_IN}], "instant_withdrawal": {"order": [{"params": [{"value": STAND_IN}]}]}}
    assert resolve_strategy(raw, c, "mid", {}) == raw
    out = json.dumps(resolve_strategy(raw, c, "mid", {"leaf": CHILD}))
    assert STAND_IN[2:] not in out.lower() and out.count("0x00000000000000000000000000000000000000D2") == 3
    assert resolve_strategy(raw, c, "leaf", {"leaf": CHILD}) == raw      # only a parent's own links apply


def test_a_market_link_adds_the_child_next_to_the_existing_ones(tmp_path):
    links = [{"parent": "mid", "child": "leaf", "kind": "erc4626", "market": "ERC4626_0001"}]
    c = load_campaign(_write(tmp_path, links))
    raw = {"substrates": [{"market": "ERC4626_0001", "encoding": "address", "values": [STAND_IN]}],
           "price_feeds": [{"asset": STAND_IN, "feed_type": "ERC4626PriceFeedFactory", "feed": None, "params": {}}]}
    out = resolve_strategy(raw, c, "mid", {"leaf": CHILD})
    assert [v.lower() for v in out["substrates"][0]["values"]] == [STAND_IN, CHILD]
    assert [f["asset"].lower() for f in out["price_feeds"]] == [STAND_IN, CHILD]
    assert resolve_strategy(out, c, "mid", {"leaf": CHILD}) == out           # idempotent
    with pytest.raises(ValueError, match="no substrates for market"):
        resolve_strategy({"substrates": []}, c, "mid", {"leaf": CHILD})
