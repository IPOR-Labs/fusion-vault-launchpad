"""deploy.rehearsal_rules — the pure half of the fork rehearsal (SDK-free)."""
import pytest

from deploy.rehearsal_rules import (
    DEFAULT_MAX_NAV_DRIFT_BPS,
    RehearsalBatch,
    bps_of,
    coverage_problems,
    deposit_problems,
    execute_problems,
    functional_fuses,
    max_drift_bps,
    resolve_deposit_amount,
    withdrawal_mode,
    withdrawal_problems,
)

USDC = 10**6


def _cfg(fuses=("AaveV3SupplyFuse",), window=0, rehearsal=None):
    raw = {"fuses": [{"name": f} for f in fuses], "withdraw_manager": {"window_seconds": window}}
    if rehearsal is not None:
        raw["rehearsal"] = rehearsal
    return raw


def test_bps_of():
    assert bps_of(0, 0) == 0.0
    assert bps_of(5, 0) == float("inf")
    assert bps_of(-100, 10_000) == 100.0


def test_withdrawal_mode_follows_the_window():
    assert withdrawal_mode(_cfg(window=0)) == "instant"
    assert withdrawal_mode(_cfg(window=604800)) == "scheduled"
    assert withdrawal_mode({}) == "instant"


def test_deposit_amount_and_drift_defaults():
    assert resolve_deposit_amount(_cfg(), 6) == 1_000 * USDC
    assert resolve_deposit_amount(_cfg(rehearsal={"deposit_underlying": "10000"}), 6) == 10_000 * USDC
    assert resolve_deposit_amount(_cfg(rehearsal={"deposit_underlying": "0.5"}), 18) == 5 * 10**17
    assert max_drift_bps(_cfg()) == DEFAULT_MAX_NAV_DRIFT_BPS
    assert max_drift_bps(_cfg(rehearsal={"max_nav_drift_bps": 25})) == 25


# --- coverage ---

def test_every_declared_fuse_must_be_exercised():
    cfg = _cfg(fuses=("MorphoFlashLoanFuse", "MorphoBorrowFuse"))
    batches = [RehearsalBatch("open", [], ["MorphoFlashLoanFuse"])]
    p = coverage_problems(cfg, batches)
    assert len(p) == 1 and "MorphoBorrowFuse declared but never executed" in p[0]
    assert coverage_problems(cfg, [RehearsalBatch("open", [], ["MorphoFlashLoanFuse", "MorphoBorrowFuse"])]) == []


def test_allow_unexercised_opt_out_and_undeclared_exercise():
    cfg = _cfg(fuses=("AaveV3SupplyFuse", "MerklClaimFuse"), rehearsal={"allow_unexercised": ["MerklClaimFuse"]})
    assert coverage_problems(cfg, [RehearsalBatch("s", [], ["AaveV3SupplyFuse"])]) == []
    p = coverage_problems(cfg, [RehearsalBatch("s", [], ["AaveV3SupplyFuse", "GhostFuse"])])
    assert any("does not declare" in x for x in p)


def test_functional_fuses_lists_declared_names():
    assert functional_fuses(_cfg(fuses=("A", "B"))) == ["A", "B"]


# --- deposit ---

def test_deposit_credited_exactly_passes_and_double_count_fails():
    assert deposit_problems(1_000 * USDC, 0, 1_000 * USDC, 10**8) == []
    assert deposit_problems(1_000 * USDC, 0, 1_000 * USDC - 1, 10**8) == []          # share rounding
    p = deposit_problems(1_000 * USDC, 0, 2_000 * USDC, 10**8)
    assert p and "counted twice" in p[0]
    assert any("no shares" in x for x in deposit_problems(1_000 * USDC, 0, 1_000 * USDC, 0))


# --- execute ---

def test_execute_within_drift_and_consistent_cache_passes():
    nav = 10_000 * USDC
    after = nav - 30 * USDC   # 30 bps swap cost
    assert execute_problems("loop", nav, after, after, {"MORPHO": after, "ERC20": 0}, 100) == []


def test_execute_nav_jump_up_is_double_counting():
    nav = 10_000 * USDC
    p = execute_problems("loop", nav, nav * 2, nav * 2, {}, 100)
    assert p and "counted twice" in p[0] and "up" in p[0]


def test_execute_nav_drop_beyond_limit_is_value_loss():
    nav = 10_000 * USDC
    p = execute_problems("loop", nav, nav // 2, nav // 2, {}, 100)
    assert p and "vanished" in p[0]


def test_execute_cached_vs_refreshed_mismatch_is_a_graph_problem():
    nav = 10_000 * USDC
    p = execute_problems("loop", nav, nav, nav - 500 * USDC, {}, 100)
    assert p and "dependency graph" in p[0]


def test_execute_market_worth_more_than_the_vault_is_flagged():
    nav = 10_000 * USDC
    p = execute_problems("loop", nav, nav, nav, {"MORPHO": nav + 10 * USDC}, 100)
    assert p and "MORPHO" in p[0] and "counted twice" in p[0]


# --- withdrawal ---

def test_withdrawal_paid_in_full_passes():
    assert withdrawal_problems(500 * USDC, 500 * USDC, 10_000 * USDC, 9_500 * USDC) == []
    # share rounding on redeemFromRequest: 5,215 raw short on 14,970 USDC (0.003 bps) is not a shortfall
    assert withdrawal_problems(14_970_020_373, 14_970_015_158, 29_940_040_996, 29_940_040_996 - 14_970_015_158) == []


def test_withdrawal_short_or_nav_mismatch_fails():
    assert any("paid" in x for x in withdrawal_problems(500 * USDC, 400 * USDC, 10_000 * USDC, 9_600 * USDC))
    assert any("moved totalAssets" in x for x in withdrawal_problems(500 * USDC, 500 * USDC, 10_000 * USDC, 9_000 * USDC))
