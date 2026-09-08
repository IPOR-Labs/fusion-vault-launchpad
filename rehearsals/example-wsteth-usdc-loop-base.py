"""Rehearsal script for strategies/example-wsteth-usdc-loop-base.json.

Mirrors the SDK's own worked loop (ipor-fusion.py tests/test_simulate_looping_morpho_blue_base.py):
one flash-loan-wrapped FuseAction tree opens the position, a second one unwinds it so
the scheduled withdrawal can be paid. Only the swap route differs (Uniswap SwapRouter02,
USDC -> WETH -> wstETH), because that is the target this strategy grants.

The engine calls build_batches(env, "open") after the deposit and build_batches(env,
"unwind") before the withdrawal. Every declared fuse must appear in some batch's
`exercises`, or the rehearsal fails coverage.
"""
from __future__ import annotations

from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector as sel

from ipor_fusion.fuses import (
    MorphoBorrowFuse,
    MorphoCollateralFuse,
    MorphoFlashLoanFuse,
    UniversalTokenSwapperFuse,
)

from deploy.rehearsal_rules import RehearsalBatch

MORPHO_MARKET = "0x13c42741a359ac4a8aa8287d2be109dcf28344484f91185f9a79bd5a805a55ae"  # wstETH / USDC, LLTV 86%
UNI_ROUTER = "0x2626664c2603336E57B271c5C0b26F421741e481"  # Uniswap SwapRouter02 on Base
FLASH_TO_DEPOSIT = 1.5  # 2.5x notional on the deposit, ~60% LTV under the 86% LLTV


def _uniswap_exact_input(env, token_in, path, amount_in):
    """approve(router) + SwapRouter02.exactInput(path, recipient=vault, amountIn, minOut=0);
    the vault-side slippage cap of the swapper fuse still applies."""
    approve = sel("approve(address,uint256)") + encode(["address", "uint256"], [UNI_ROUTER, amount_in])
    swap = sel("exactInput((bytes,address,uint256,uint256))") + encode(
        ["(bytes,address,uint256,uint256)"], [(path, env.vault_address, amount_in, 0)]
    )
    return [token_in, UNI_ROUTER], [approve, swap]


def _path(*hops):
    """(tokenA, fee, tokenB, fee, tokenC) -> packed Uniswap V3 path."""
    out = b""
    for i, h in enumerate(hops):
        out += bytes.fromhex(h[2:]) if i % 2 == 0 else int(h).to_bytes(3, "big")
    return out


def build_batches(env, stage: str) -> list[RehearsalBatch]:
    usdc, weth, wsteth = env.token("USDC"), env.token("WETH"), env.token("wstETH")
    flash = MorphoFlashLoanFuse(env.fuse("MorphoFlashLoanFuse"))
    collateral = MorphoCollateralFuse(env.fuse("MorphoCollateralFuse"))
    borrow = MorphoBorrowFuse(env.fuse("MorphoBorrowFuse"))
    swapper = UniversalTokenSwapperFuse(env.fuse("SwapFuseUniversalTokenSwapper"))
    exercises = ["MorphoFlashLoanFuse", "SwapFuseUniversalTokenSwapper", "MorphoCollateralFuse", "MorphoBorrowFuse"]

    if stage == "open":
        # Size the loop from the rehearsal deposit, not from everything idle: a re-run on a used
        # fork vault would otherwise swap ever larger amounts through the same pools until the
        # swapper's oracle-vs-execution slippage check fails (UniversalTokenSwapperFuseSlippageFail).
        idle = min(env.balance_of(usdc), env.deposit)
        flash_amount = int(idle * FLASH_TO_DEPOSIT)
        total_in = idle + flash_amount
        targets, data = _uniswap_exact_input(env, usdc, _path(usdc, 500, weth, 100, wsteth), total_in)
        loop = flash.flash_loan(asset=usdc, amount=flash_amount, actions=[
            swapper.swap(token_in=usdc, token_out=wsteth, amount_in=total_in, targets=targets, data=data),
            collateral.supply_collateral(market_id=MORPHO_MARKET, amount=10**30),  # fuse clamps to balance
            borrow.borrow(market_id=MORPHO_MARKET, amount=flash_amount),
        ])
        return [RehearsalBatch("open 2.5x wstETH/USDC loop", [loop], exercises)]

    if stage == "unwind":
        mid = bytes.fromhex(MORPHO_MARKET[2:])
        morpho = env.deploy_ctx.resolve_address("morpho_blue")
        _, borrow_shares, coll = env.call(morpho, "position(bytes32,address)", ["bytes32", "address"], [mid, env.vault_address],
                                          ["uint256", "uint128", "uint128"])
        m = env.call(morpho, "market(bytes32)", ["bytes32"], [mid], ["uint128"] * 6)
        debt = -(-borrow_shares * m[2] // m[3]) if m[3] else 0  # ceil(shares * totalBorrowAssets / totalBorrowShares)
        if borrow_shares == 0 and coll == 0:
            return []
        flash_amount = debt + debt // 1000 + 10**6  # accrued interest + 1 USDC buffer
        repay_all = borrow._action_raw("exit((bytes32,uint256,uint256))", [[mid, 0, borrow_shares]])  # by shares: exact
        targets, data = _uniswap_exact_input(env, wsteth, _path(wsteth, 100, weth, 500, usdc), coll)
        unwind = flash.flash_loan(asset=usdc, amount=flash_amount, actions=[
            repay_all,
            collateral.withdraw_collateral(market_id=MORPHO_MARKET, amount=coll),
            swapper.swap(token_in=wsteth, token_out=usdc, amount_in=coll, targets=targets, data=data),
        ])
        return [RehearsalBatch("unwind loop to idle USDC", [unwind], exercises)]

    raise ValueError(f"unknown stage {stage!r}")
