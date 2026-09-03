"""Pipeline steps. Each module exposes `NAME` and
`run(cfg, deploy_ctx, session, instance, state, broadcast)`. Steps record their
intended on-chain actions via `session.recorder` (see deploy/plan.py) so a run
produces a machine-readable plan/run artifact."""
from importlib import import_module
from pathlib import Path

_STEP_MODULES = [
    "s00_dry_run_report",
    "s01_clone",
    "s01b_bootstrap_roles",
    "s01c_total_supply_cap",
    "s02_add_fuses",
    "s02b_standard_fuses",
    "s03_grant_substrates",
    "s04_balance_fuses",
    "s05_price_feeds",
    "s06_withdraw_manager",
    "s07_fees",
    "s08_instant_withdrawal",
    "s09_pre_hooks",
    "s10_whitelist",
    "s11_roles",
    "s12_transferability",
]


def all_steps():
    return [import_module(f"deploy.steps.{name}") for name in _STEP_MODULES]
