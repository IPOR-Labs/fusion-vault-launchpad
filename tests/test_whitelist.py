"""deploy.whitelist — FuseWhitelist gate (pure parts; chain readers are exercised by --verify-only)."""
from eth_abi import encode

from deploy.whitelist import (FuseStatus, whitelist_problems, decode_fuse_by_address,
                              config_fuse_refs, STATE_ACTIVE, STATE_DEPRECATED, STATE_REMOVED, STATE_DEFAULT)

A1 = "0x0000000000000000000000000000000000000001"
A2 = "0x0000000000000000000000000000000000000002"


def _st(name, addr, state, listed=True, t="X"):
    return FuseStatus(name=name, address=addr, listed=listed, state=state, type_name=t)


def test_active_passes_and_unlisted_removed_deprecated_fail():
    fails, warns = whitelist_problems([_st("a", A1, STATE_ACTIVE)])
    assert fails == [] and warns == []
    fails, _ = whitelist_problems([_st("u", A1, 0, listed=False), _st("r", A2, STATE_REMOVED), _st("d", A2, STATE_DEPRECATED)])
    assert len(fails) == 3
    assert "NOT on the FuseWhitelist" in fails[0] and "removed" in fails[1] and "deprecated" in fails[2]


def test_default_state_is_a_warning_not_a_failure():
    fails, warns = whitelist_problems([_st("x", A1, STATE_DEFAULT)])
    assert fails == [] and len(warns) == 1 and "default" in warns[0]


def test_decode_fuse_by_address_listed_and_unlisted():
    types = {91: "UniversalTokenSwapperFuse"}
    ret = encode(["uint16", "uint16", "address", "uint32"], [3, 91, A1, 1700000000])
    s = decode_fuse_by_address("UniversalTokenSwapperFuse", A1, ret, types)
    assert s.listed and s.state == STATE_REMOVED and s.type_name == "UniversalTokenSwapperFuse" and s.state_name == "removed"
    zero = encode(["uint16", "uint16", "address", "uint32"], [0, 0, "0x" + "00" * 20, 0])
    s = decode_fuse_by_address("ghost", A2, zero, types)
    assert not s.listed and s.type_name == ""
    assert whitelist_problems([s])[0]


class _Ctx:
    _F = {"AaveV3SupplyFuse": A1, "AaveV3BalanceFuse": A2, "ERC20BalanceFuse": "0x0000000000000000000000000000000000000003"}
    def fuse(self, name): return self._F[name]


def test_config_fuse_refs_covers_functional_balance_and_queue_fuses_once():
    cfg = {"fuses": [{"name": "AaveV3SupplyFuse"}],
           "balance_fuses": [{"market": "AAVE_V3", "fuse": "AaveV3BalanceFuse"}, {"market": "ERC20_VAULT_BALANCE", "fuse": "ERC20BalanceFuse"}],
           "instant_withdrawal": {"order": [{"fuse": "AaveV3SupplyFuse", "params": []}]},
           "pre_hooks": [{"name": "PauseFunctionPreHook"}]}
    refs = config_fuse_refs(cfg, _Ctx())
    assert [n for n, _ in refs] == ["AaveV3SupplyFuse", "AaveV3BalanceFuse", "ERC20BalanceFuse"]
