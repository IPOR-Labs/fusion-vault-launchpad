"""deploy.ipor_abi + deploy.context + deploy.address_book — addresses come from ipor-abi, not the context (offline)."""
import datetime as dt
import json

import jsonschema
import pytest

from deploy.address_book import FAIL, OK, WARN, address_rows
from deploy.context import CONTEXTS_DIR, load_context
from deploy.ipor_abi import load_snapshot, snapshot_diff, snapshot_document

FACTORY = "0x00000000000000000000000000000000000000F1"
POM = "0x00000000000000000000000000000000000000F2"
WL = "0x00000000000000000000000000000000000000F3"
OWNER = "0x00000000000000000000000000000000000000F4"
BURN1 = "0x00000000000000000000000000000000000000B1"
BURN2 = "0x00000000000000000000000000000000000000B2"
HANDLER = "0x00000000000000000000000000000000000000C1"
HOOK = "0x00000000000000000000000000000000000000D1"
FEEDF = "0x00000000000000000000000000000000000000E1"
SUPPLY = "0x00000000000000000000000000000000000000A1"
PINNED = "0x00000000000000000000000000000000000000A2"
PROVIDER = "0x0000000000000000000000000000000000000AA1"
POOL = "0x0000000000000000000000000000000000000AA2"
USDC = "0x0000000000000000000000000000000000000CC1"

ADDRESSES = {
    "IporFusionFactoryProxy": FACTORY, "PriceOracleMiddlewareUsdWithRolesProxy": POM,
    "IporFusionFuseWhitelistProxy": WL, "IporFusionPriceOracleMiddlewareWithRolesOwner": OWNER,
    "BurnRequestFeeFuse": BURN1, "BurnRequestFeeFuseV2": BURN2, "CallbackHandlerMorpho": HANDLER,
    "PreHookPauseFunction": HOOK, "ERC4626PriceFeedFactoryProxy": FEEDF, "SupplyFuseAaveV3": SUPPLY,
    "AaveV3PoolAddressesProvider": PROVIDER, "Morpho": "0x00000000000000000000000000000000000000F5",
}
CONTEXT = {
    "$schema": "../schema/deploy-context.schema.json", "public_rpc": None,
    "pins": {"PinnedFuse": {"address": PINNED, "reason": "two versions active; the SDK encodes this one", "checked": "2026-10-05"}},
    "external_addresses": {"aave_v3_pool": {"address": POOL, "source": "AaveV3PoolAddressesProvider.getPool()"}},
    "tokens": {"USDC": USDC},
}


@pytest.fixture
def ctx(tmp_path):
    (tmp_path / "ipor-abi").mkdir()
    (tmp_path / "ipor-abi" / "mainnet-test-fusion.json").write_text(
        json.dumps(snapshot_document("mainnet-test-fusion", ADDRESSES, "a" * 40, "2026-10-01")))
    (tmp_path / "test-fusion.json").write_text(json.dumps(CONTEXT))
    return load_context("test-fusion", tmp_path, chain_id=8453)


class Reader:
    def __init__(self, **over):
        self.cid = over.get("cid", 8453)
        self.nocode = {a.lower() for a in over.get("nocode", [])}
        self.getters = {(FACTORY.lower(), "getPriceOracleMiddleware()"): over.get("pom", POM),
                        (FACTORY.lower(), "getBurnRequestFeeFuseAddress()"): over.get("burn", BURN2),
                        (PROVIDER.lower(), "getPool()"): over.get("pool", POOL)}
        self.states = over.get("states", {})
        self.symbols = {USDC.lower(): over.get("usdc_symbol", "USDC")}

    def chain_id(self): return self.cid
    def has_code(self, a): return a.lower() not in self.nocode
    def address_getter(self, c, sig): return self.getters.get((c.lower(), sig))
    def symbol(self, t): return self.symbols.get(t.lower())
    def whitelist_entry(self, wl, a): return self.states.get(a.lower(), ("active", "T"))


def _verdicts(rows, category=None):
    return {(r.category, r.name): r.verdict for r in rows if category is None or r.category == category}


def test_context_resolves_every_address_through_the_snapshot(ctx):
    assert ctx.fusion_factory.lower() == FACTORY.lower() and ctx.fuse_whitelist.lower() == WL.lower()
    assert ctx.price_oracle_middleware.lower() == POM.lower() and ctx.middleware_owner.lower() == OWNER.lower()
    assert [a.lower() for a in ctx.standard_fuses()] == [BURN1.lower(), BURN2.lower()]
    assert ctx.callback_handler("CallbackHandlerMorpho").lower() == HANDLER.lower()
    assert ctx.pre_hook("PauseFunctionPreHook").lower() == HOOK.lower()
    assert ctx.price_feed_factory("ERC4626PriceFeedFactory").lower() == FEEDF.lower()
    assert ctx.resolve_address("morpho_blue") and ctx.resolve_address("aave_v3_pool").lower() == POOL.lower()
    assert ctx.resolve_address("USDC").lower() == USDC.lower()


def test_fuse_lookup_order_is_whitelist_override_then_pin_then_ipor_abi(ctx):
    assert ctx.fuse("SupplyFuseAaveV3").lower() == SUPPLY.lower()   # an ipor-abi key
    assert ctx.fuse("PinnedFuse").lower() == PINNED.lower()
    with pytest.raises(KeyError, match="not resolved on the FuseWhitelist"):
        ctx.fuse("AaveV3SupplyFuse")             # a whitelist type name: only the resolver can map it
    ctx.set_fuse_override("AaveV3SupplyFuse", PINNED)
    assert ctx.fuse("AaveV3SupplyFuse").lower() == PINNED.lower()


def test_a_key_missing_from_the_snapshot_names_the_fix(ctx):
    with pytest.raises(KeyError, match="make ipor-abi"):
        ctx.callback_handler("CallbackHandlerEulerV9")


def test_no_overrides_file_is_needed(tmp_path):
    (tmp_path / "ipor-abi").mkdir()
    (tmp_path / "ipor-abi" / "mainnet-bare-fusion.json").write_text(
        json.dumps(snapshot_document("mainnet-bare-fusion", ADDRESSES, "a" * 40, "2026-10-01")))
    ctx = load_context("bare-fusion", tmp_path, chain_id=1)
    assert ctx.raw == {} and ctx.chain_id == 1 and ctx.fusion_factory.lower() == FACTORY.lower()
    assert ctx.pre_hook("PauseFunctionPreHook").lower() == HOOK.lower()      # legacy name, built-in alias
    assert ctx.pre_hook("PreHookPauseFunction").lower() == HOOK.lower()      # the ipor-abi key itself
    assert ctx.public_rpc is None and ctx.pins() == {}


def test_an_alias_and_a_contract_override_win_over_the_defaults(ctx):
    ctx.raw["aliases"] = {"MySupply": "SupplyFuseAaveV3"}
    ctx.raw["contracts"] = {"fusion_factory": "PreHookPauseFunction"}
    assert ctx.fuse("MySupply").lower() == SUPPLY.lower()
    assert ctx.fusion_factory.lower() == HOOK.lower()


def test_a_clean_chain_passes(ctx):
    rows = address_rows(ctx, Reader(), today=dt.date(2026, 10, 5))
    assert [r for r in rows if r.verdict != OK] == []


def test_wrong_chain_and_root_mismatch_fail(ctx):
    v = _verdicts(address_rows(ctx, Reader(cid=1, pom=HOOK), today=dt.date(2026, 10, 5)))
    assert v[("chain", "chain_id")] == FAIL
    assert v[("contract", "factory.getPriceOracleMiddleware()")] == FAIL


def test_missing_code_fails_for_handlers_hooks_factories_and_externals(ctx):
    rows = address_rows(ctx, Reader(nocode=[HANDLER, HOOK, FEEDF, POOL]), today=dt.date(2026, 10, 5))
    fails = {(r.category, r.name) for r in rows if r.verdict == FAIL}
    assert {("callback_handler", "CallbackHandlerMorpho"), ("pre_hook", "PreHookPauseFunction"),
            ("price_feed_factory", "ERC4626PriceFeedFactory"), ("external", "aave_v3_pool")} <= fails


def test_aave_pool_must_match_the_provider(ctx):
    v = _verdicts(address_rows(ctx, Reader(pool=SUPPLY), today=dt.date(2026, 10, 5)))
    assert v[("external", "AaveV3PoolAddressesProvider.getPool()")] == FAIL


def test_soft_problems_warn(ctx):
    rows = address_rows(ctx, Reader(burn=HOOK, usdc_symbol="USDT", states={BURN2.lower(): ("deprecated", "BurnRequestFeeFuse")}),
                        today=dt.date(2026, 12, 31))
    warns = {(r.category, r.name) for r in rows if r.verdict == WARN}
    assert ("snapshot", "mainnet-test-fusion") in warns                       # 91 days old
    assert ("standard_fuse", "BurnRequestFeeFuseV2") in warns                  # newest standard fuse deprecated
    assert ("standard_fuse", "factory.getBurnRequestFeeFuseAddress()") in warns
    assert ("token", "USDC") in warns
    assert not [r for r in rows if r.verdict == FAIL]


def test_only_the_newest_standard_fuse_must_be_active(ctx):
    rows = address_rows(ctx, Reader(states={BURN1.lower(): ("deprecated", "BurnRequestFeeFuse")}), today=dt.date(2026, 10, 5))
    assert [r for r in rows if r.verdict != OK] == []


def test_snapshot_diff_and_reverse_lookup(tmp_path):
    (tmp_path / "d.json").write_text(json.dumps(snapshot_document("d", {"A": FACTORY, "B": FACTORY}, "b" * 40, "2026-10-05")))
    s = load_snapshot("d", tmp_path)
    assert s.keys_for(FACTORY.lower()) == ["A", "B"] and s.get("C") is None and s.commit == "b" * 40
    assert snapshot_diff({"A": "1", "B": "2"}, {"B": "3", "C": "4"}) == (["C"], ["A"], ["B"])


@pytest.mark.parametrize("path", sorted(p for p in CONTEXTS_DIR.glob("*.json")), ids=lambda p: p.stem)
def test_shipped_overrides_validate_and_resolve_offline(path):
    schema = json.loads((CONTEXTS_DIR.parent / "schema" / "deploy-context.schema.json").read_text())
    raw = json.loads(path.read_text())
    jsonschema.validate(raw, schema)
    ctx = load_context(path.stem)
    for ref in ctx.contract_refs():
        assert ctx.resolve_address(ref)
    assert ctx.chain_id and ctx.standard_fuses()
    for name in raw.get("tokens", {}):
        assert ctx.token(name)
    keys = ctx.snapshot.addresses
    for k in keys:
        if k.startswith("CallbackHandler"):
            assert ctx.callback_handler(k)
        if k.endswith("PriceFeedFactoryProxy"):
            assert ctx.price_feed_factory(k[:-len("Proxy")])
