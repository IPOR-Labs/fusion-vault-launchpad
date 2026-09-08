"""Rehearsal script for strategies/example-usdc-aave-base.json.

The single-venue case, as in the SDK's guide walk (ipor-fusion.py
skills/ipor-deploy-vault/SKILL.md and tests/test_simulate_vault_from_scratch_base.py):
supply the deposited USDC to Aave V3. No unwind: the instant-withdrawal queue pulls
from Aave when the rehearsal withdraws, which is exactly what it must prove.
"""
from __future__ import annotations

from ipor_fusion.fuses import AaveV3SupplyFuse

from deploy.rehearsal_rules import RehearsalBatch


def build_batches(env, stage: str) -> list[RehearsalBatch]:
    if stage != "open":
        return []
    usdc = env.token("USDC")
    supply = AaveV3SupplyFuse(env.fuse("AaveV3SupplyFuse"))
    amount = env.balance_of(usdc) - 10**6  # keep 1 USDC idle: Aave scaled-balance rounding
    return [RehearsalBatch("supply USDC to Aave V3", [supply.supply(asset=usdc, amount=amount)], ["AaveV3SupplyFuse"])]
