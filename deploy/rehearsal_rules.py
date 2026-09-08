"""Rehearsal rules — the pure half of "verified".

A configured vault is not verified until it has been *used* on a fork: a deposit
credited 1:1, every declared fuse executed against its granted substrates, the
accounting checked for double counting, and a withdrawal paid out. The chain
I/O lives in ``deploy/rehearsal.py``; every decision it makes comes from here,
so the unit suite can pin the thresholds and the failure messages.

Numbers are raw underlying units (no decimals scaling), exactly as the vault
reports them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Rounding slack for "exact" comparisons (deposit credited, cache == fresh),
# in basis points of the amount compared. ERC-4626 share maths rounds down by
# a unit; anything beyond this is a real discrepancy.
DUST_BPS = 1

# Default ceiling on NAV movement across one execute batch. Swaps and lending
# entry cost something; a leveraged loop with two swaps stayed under 40 bps on
# Base (2026-09-08). A batch that moves NAV more than this either double counts
# or destroys value, and either way the human must look.
DEFAULT_MAX_NAV_DRIFT_BPS = 100


@dataclass(slots=True)
class RehearsalBatch:
    """One ``execute()`` the rehearsal script wants sent, with the fuses it exercises.

    ``exercises`` is declared by the script rather than inferred from the action
    tree because nested flash-loan actions are encoded bytes by the time the
    engine sees them; the coverage check trusts the declaration and the fork
    proves it (an action through an unregistered fuse reverts).
    """
    label: str
    actions: list[Any]
    exercises: list[str] = field(default_factory=list)


def bps_of(delta: int, base: int) -> float:
    """|delta| as basis points of base; ``inf`` when base is zero and delta is not."""
    if base == 0:
        return 0.0 if delta == 0 else float("inf")
    return abs(delta) * 10_000 / base


def withdrawal_mode(cfg_raw: dict) -> str:
    """``instant`` when no scheduled window is configured, else ``scheduled``."""
    window = int(cfg_raw.get("withdraw_manager", {}).get("window_seconds", 0) or 0)
    return "scheduled" if window > 0 else "instant"


def functional_fuses(cfg_raw: dict) -> list[str]:
    """Declared functional fuses (``fuses[]``); balance fuses are exercised implicitly."""
    return [f["name"] for f in cfg_raw.get("fuses", [])]


def coverage_problems(cfg_raw: dict, batches: list[RehearsalBatch]) -> list[str]:
    """Every declared functional fuse must be exercised by some batch, unless the
    strategy lists it under ``rehearsal.allow_unexercised`` (claim fuses whose
    rewards do not exist on a fresh fork are the usual case)."""
    exercised = {name for b in batches for name in b.exercises}
    allowed = set(cfg_raw.get("rehearsal", {}).get("allow_unexercised", []))
    unknown = exercised - set(functional_fuses(cfg_raw))
    problems = [f"batch exercises {sorted(unknown)} which the strategy does not declare under fuses[]"] if unknown else []
    for name in functional_fuses(cfg_raw):
        if name not in exercised and name not in allowed:
            problems.append(f"{name} declared but never executed on the fork — a granted substrate the fuse cannot act on "
                            f"would pass every read-back check; add a batch that uses it, or list it under rehearsal.allow_unexercised")
    return problems


def deposit_problems(deposit: int, nav_before: int, nav_after: int, shares_minted: int) -> list[str]:
    """Deposit must credit NAV by exactly the deposit (share rounding aside) and mint shares."""
    problems = []
    delta = nav_after - nav_before
    if bps_of(delta - deposit, deposit) > DUST_BPS:
        problems.append(f"deposit of {deposit} moved totalAssets by {delta} (expected the deposit within {DUST_BPS} bps): "
                        f"an asset is counted twice, a fee hook fired, or the vault cannot price the underlying")
    if shares_minted <= 0:
        problems.append("deposit minted no shares")
    return problems


def execute_problems(label: str, nav_before: int, nav_after: int, nav_fresh: int,
                     market_values: dict[str, int], max_drift_bps: int) -> list[str]:
    """After one execute batch:

    * NAV may move only by the batch's real cost (``max_drift_bps``): a jump up is
      double counting, a drop beyond cost is value destruction or a missing balance fuse;
    * the cached NAV the post-execute hook stored must equal a fresh
      ``updateMarketsBalances`` read (else the dependency graph is incomplete);
    * no single market may be worth more than the whole vault.
    """
    problems = []
    drift = bps_of(nav_after - nav_before, nav_before)
    if drift > max_drift_bps:
        direction = "up" if nav_after > nav_before else "down"
        problems.append(f"{label}: totalAssets moved {direction} {drift:.1f} bps (limit {max_drift_bps}) — "
                        + ("value appeared from nowhere: a market is counted twice" if nav_after > nav_before
                           else "value vanished: a position is not valued by any balance fuse, or the batch overpaid"))
    if bps_of(nav_fresh - nav_after, nav_after) > DUST_BPS:
        problems.append(f"{label}: cached totalAssets {nav_after} != refreshed {nav_fresh} — the post-execute hook did not "
                        f"re-measure every touched market (dependency graph incomplete)")
    for market, value in market_values.items():
        if value > nav_fresh + max(1, nav_fresh * DUST_BPS // 10_000):
            problems.append(f"{label}: market {market} valued at {value} > totalAssets {nav_fresh} — counted twice")
    return problems


def withdrawal_problems(requested: int, paid: int, nav_before: int, nav_after: int) -> list[str]:
    """The withdrawer must receive what was requested and NAV must drop by that amount."""
    problems = []
    if paid < requested and bps_of(requested - paid, requested) > DUST_BPS:
        problems.append(f"withdrawal paid {paid} of the {requested} requested")
    if bps_of((nav_before - nav_after) - paid, max(paid, 1)) > DUST_BPS:
        problems.append(f"withdrawal of {paid} moved totalAssets by {nav_before - nav_after} (expected the payout within {DUST_BPS} bps)")
    return problems


def resolve_deposit_amount(cfg_raw: dict, underlying_decimals: int) -> int:
    """``rehearsal.deposit_underlying`` (human units) in raw units; defaults to 1,000."""
    from decimal import Decimal
    human = str(cfg_raw.get("rehearsal", {}).get("deposit_underlying", "1000"))
    return int(Decimal(human) * (Decimal(10) ** underlying_decimals))


def max_drift_bps(cfg_raw: dict) -> int:
    return int(cfg_raw.get("rehearsal", {}).get("max_nav_drift_bps", DEFAULT_MAX_NAV_DRIFT_BPS))
