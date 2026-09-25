"""deploy.fuse_resolver — whitelist-first fuse resolution (pure parts)."""
from deploy.fuse_resolver import FuseRef, config_refs, resolve_refs

A_OLD = "0x000000000000000000000000000000000000000A"   # deprecated version, still in the context
A_NEW = "0x000000000000000000000000000000000000000b"   # active successor on the whitelist
B1 = "0x00000000000000000000000000000000000000B1"
B2 = "0x00000000000000000000000000000000000000B2"
E = "0x00000000000000000000000000000000000000EE"
MARKETS = {"ERC4626_0001": 100001, "ERC4626_0002": 100002, "ERC20_VAULT_BALANCE": 7, "EULER_V2": 11}


class _Index:
    types = {"Erc4626SupplyFuse": 20, "ERC20BalanceFuse": 23, "EulerV2SupplyFuse": 40}
    by_addr = {A_OLD.lower(): (2, 20)}           # deprecated, type Erc4626SupplyFuse
    hits = {(20, 100001): [A_NEW], (20, 100002): [B1, B2], (23, 7): [E], (40, 11): []}

    def type_id(self, n): return self.types.get(n)
    def type_of_address(self, a): return self.by_addr.get(a.lower())
    def active_(self, t, m): return self.hits.get((t, m), [])
    def active(self, t, m): return self.active_(t, m)


def _cs(a): return a[:2] + a[2:].lower()


def test_stale_context_address_is_replaced_by_the_active_successor_with_a_warning():
    refs = [FuseRef("SupplyFuseErc4626Market1", None, "fuse"), FuseRef("ERC20BalanceFuse", "ERC20_VAULT_BALANCE", "balance")]
    res = resolve_refs(refs, _Index(), {"SupplyFuseErc4626Market1": A_OLD}, MARKETS.__getitem__, ["ERC4626_0001", "ERC20_VAULT_BALANCE"])
    assert res.fails == []
    assert res.overrides["SupplyFuseErc4626Market1"].lower() == A_NEW.lower()
    assert res.overrides["ERC20BalanceFuse"].lower() == E.lower()
    assert any("update the context" in w for w in res.warns)


def test_type_name_resolves_without_any_context_entry():
    res = resolve_refs([FuseRef("Erc4626SupplyFuse", "ERC4626_0001")], _Index(), {}, MARKETS.__getitem__, [])
    assert res.fails == [] and res.overrides["Erc4626SupplyFuse"].lower() == A_NEW.lower()


def test_ambiguous_markets_need_an_explicit_market():
    res = resolve_refs([FuseRef("Erc4626SupplyFuse")], _Index(), {}, MARKETS.__getitem__, ["ERC4626_0001", "ERC4626_0002"])
    assert res.overrides == {} and res.fails and 'add "market"' in res.fails[0]


def test_several_active_on_one_market_keeps_the_context_pin_or_fails():
    res = resolve_refs([FuseRef("Erc4626SupplyFuse", "ERC4626_0002")], _Index(), {"Erc4626SupplyFuse": B2}, MARKETS.__getitem__, [])
    assert res.fails == [] and res.overrides["Erc4626SupplyFuse"].lower() == B2.lower()
    res = resolve_refs([FuseRef("Erc4626SupplyFuse", "ERC4626_0002")], _Index(), {}, MARKETS.__getitem__, [])
    assert res.fails and "pin one" in res.fails[0]


def test_no_active_fuse_falls_back_to_the_context_with_a_warning_or_fails():
    res = resolve_refs([FuseRef("EulerV2SupplyFuse", "EULER_V2")], _Index(), {"EulerV2SupplyFuse": B1}, MARKETS.__getitem__, [])
    assert res.fails == [] and "EulerV2SupplyFuse" not in res.overrides and any("no active fuse" in w for w in res.warns)
    res = resolve_refs([FuseRef("EulerV2SupplyFuse", "EULER_V2")], _Index(), {}, MARKETS.__getitem__, [])
    assert res.fails and "no context address" in res.fails[0]


def test_unknown_name_without_context_fails_and_unlisted_context_address_passes_through():
    res = resolve_refs([FuseRef("GhostFuse")], _Index(), {}, MARKETS.__getitem__, [])
    assert res.fails and "unknown" in res.fails[0]
    res = resolve_refs([FuseRef("GhostFuse")], _Index(), {"GhostFuse": B1}, MARKETS.__getitem__, [])
    assert res.fails == [] and "GhostFuse" not in res.overrides and res.warns


def test_config_refs_collect_balance_functional_and_queue_fuses_with_markets():
    cfg = {"fuses": [{"name": "EulerV2SupplyFuse", "market": "EULER_V2"}, {"name": "X"}],
           "balance_fuses": [{"market": "ERC20_VAULT_BALANCE", "fuse": "ERC20BalanceFuse"}],
           "instant_withdrawal": {"order": [{"fuse": "X", "params": []}, {"fuse": "Q", "params": []}]}}
    refs = {r.name: r for r in config_refs(cfg)}
    assert refs["ERC20BalanceFuse"].market == "ERC20_VAULT_BALANCE" and refs["ERC20BalanceFuse"].role == "balance"
    assert refs["EulerV2SupplyFuse"].market == "EULER_V2" and refs["X"].market is None and refs["Q"].role == "queue"
