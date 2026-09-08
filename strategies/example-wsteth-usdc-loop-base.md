# Example wstETH-USDC Loop (exLOOP) — strategy spec

> **This is a worked example, not a real strategy.** Every address that holds a role, receives fees or is whitelisted is an anvil default test account whose private key is public. The pipeline refuses to broadcast this file to a live chain (`deploy/guards.py`). Copy it, rename it, and replace every placeholder before you deploy anything real.
>
> It exists to exercise the parts of the pipeline a single-venue vault never touches: a collateralised market, a flash-loan fuse with its **callback handler**, the legacy universal swapper with plain-address substrates, and a scheduled-withdrawal posture.

## Executive summary

A USDC-denominated vault on Base that runs a leveraged wstETH position on Morpho Blue: deposits in USDC are swapped to wstETH, posted as collateral on the wstETH/USDC market, and USDC is borrowed against them; a Morpho flash loan lets the Alpha open or close the whole position in one transaction. Withdrawals are scheduled (7-day window, the Alpha unwinds first), whitelisted and non-transferable at launch, DAO fee tier B. Roles are held by one operator account during the trial with a separate Guardian and a separate Alpha.

## 1. Intent `[client]`

- **Organisation**: Example Co
- **Category**: loop (leveraged staking yield)
- **Thesis**: earn the wstETH staking yield on a leveraged position while paying the Morpho USDC borrow rate; the spread is the return, ETH price risk is the exposure.
- **Target TVL at launch**: 250,000 USDC (enforced as the supply cap)

## 2. Chain and underlying `[client]`

| Item | Value | Provenance |
|---|---|---|
| Chain | Base (8453) | `[client]` |
| Underlying | USDC `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` | `[client]` |
| Context | `contexts/base-fusion.json` | `[agent: repo]` |

## 3. Venues `[agent]`

| Market (id) | Fuse(s) | Balance fuse | Substrates | Confirmed on-chain |
|---|---|---|---|---|
| `MORPHO` (14) | `MorphoCollateralFuse`, `MorphoBorrowFuse` | `MorphoBalanceFuse` | Morpho market `0x13c42741…a55ae` (wstETH collateral / USDC loan, LLTV 86%) | 2026-09-08, `FuseWhitelist` state `active` |
| `MORPHO_FLASH_LOAN` (19) | `MorphoFlashLoanFuse` | `MorphoFlashLoanBalanceFuse` | USDC (the only flash-loanable token) | 2026-09-08, `active` |
| `UNIVERSAL_TOKEN_SWAPPER` (12) | `SwapFuseUniversalTokenSwapper` | `UniversalTokenSwapperBalanceFuse` | USDC, WETH `0x4200000000000000000000000000000000000006`, wstETH `0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452`, Uniswap SwapRouter02 `0x2626664c2603336E57B271c5C0b26F421741e481` | 2026-09-08, `active`; plain-address layout confirmed on three live Base vaults |
| `ERC20_VAULT_BALANCE` (7) | — | `ERC20BalanceFuse` | USDC, wstETH | 2026-09-08, `active` |

Dependency graph: derived (every market → idle balance); no extra edges.

**Callback handler** `[agent: ipor-fusion CallbackHandlerLib]`: `MorphoFlashLoanFuse` makes Morpho call the vault back on `onMorphoFlashLoan(uint256,bytes)`. The vault only accepts that call if `CallbackHandlerMorpho` (`0x314E23a66a07644e6c3Fd1a383bc3d9351C884dD`) is registered for the Morpho Blue sender (`0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb`). Without it every other check passes and the first flash loan reverts `HandlerNotFound()`. Declared under `callback_handlers`, written by step `03b_callback_handlers`, verified from vault storage.

The swapper is the **legacy** `SwapFuseUniversalTokenSwapper` on market 12, so its substrates are plain address words (tokens and targets alike), not the typed layout of `UniversalTokenSwapperFuseV2`. The loader enforces that pairing.

## 4. Pricing `[agent]`

| Asset | Feed type | Feed / factory params | `updatedAt` checked on |
|---|---|---|---|
| USDC | `literal` | Chainlink USDC/USD `0x7e860098F58bBFC8648a4311b374B1D669a2bc6B` | 2026-09-08 |
| wstETH | `DualCrossReferencePriceFeedFactory` | Chainlink wstETH/ETH `0x43a5C292A453A3bF3606fa856197f09D7B74251a` × ETH/USD `0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70` | 2026-09-08 |

On Base the shared middleware already prices both assets, so the vault's own oracle resolves them through the fallback and step `05_price_feeds` skips both registrations (`plan_only` in the plan/run diff). The wstETH entry is still declared so a chain whose middleware lacks it deploys a correct feed rather than shipping an unpriceable collateral asset.

## 5. Roles `[client]`

| Role | Holder | Note |
|---|---|---|
| Owner, Atomist, Fuse manager, Pre-hooks manager, Whitelist, instant-withdrawal config, fee roles, oracle manager | `0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266` | anvil account #0 — **placeholder** |
| Guardian | `0x3C44CdDdB6a900fa2b585dd299e03d18d4beD2e2` | anvil account #2 — **placeholder**; must be independent of the Owner |
| Alpha, balance updater, rewards balance updater | `0x70997970C51812dc3A010C7d01b50e0d17dc79C8` | anvil account #1 — **placeholder**; the bot that opens, rebalances and unwinds the loop |

`FUSE_MANAGER_ROLE` is what `updateCallbackHandler` requires (the access-manager initializer maps it there), so the deployer must hold it during step `03b`.

Ownership model: Mode B (`deployer_temporary_owner: false`), the appointed owner is the clone owner.

## 6. Withdrawals `[client]`

- **Mode**: scheduled only. `window_seconds: 604800` (7 days).
- **Instant queue**: empty. The only market holding value is a collateralised Morpho position; pulling collateral on an instant path could liquidate the loan, so nothing is queued and only idle USDC is instantly withdrawable.
- **Contributions**: none.
- **Redemption delay**: 1 second (default, immutable).

The Alpha must unwind enough of the loop before a request's window expires; see `docs/01-concepts.md` on scheduled withdrawals.

## 7. Fees `[client]`

DAO tier B (`dao_fee_package_index: 1`). No supplemental fees. Recipient of any future supplemental fee: the operator account above.

## 8. Whitelist and transferability `[client]`

Whitelisted at launch (initial account: the operator). Shares non-transferable at launch. Both are one-way switches later; launching closed keeps every option open.

## 9. Pre-hooks `[agent]`

`ExchangeRateValidatorPreHook` on `deposit` with a 200 bps threshold. No pause hooks: the request path is the intended withdrawal route.

## 10. What the Alpha does (out of scope for the deployer)

One loop step, executed by the Alpha through the SDK inside a Morpho flash loan of USDC: swap all USDC to wstETH via SwapRouter02 (USDC → WETH → wstETH), supply the wstETH as collateral, borrow USDC to repay the flash loan. The identical configuration to this file, run on 2026-09-08 in a single `eth_simulateV1` batch against a public Base RPC (clone, configuration, 10,000 USDC deposit, one loop with a 15,000 USDC flash loan), ended at 8.15 wstETH collateral, 15,000 USDC debt, LTV 60% under the 86% LLTV, and a NAV of 9,968.70 USDC after swap costs. Fuse wrappers for the loop live in the SDK (`MorphoFlashLoanFuse`, `MorphoCollateralFuse`, `MorphoBorrowFuse`, `UniversalTokenSwapperFuse`). The SDK's own worked loop, `tests/test_simulate_looping_morpho_blue_base.py`, is the reference to copy for the Alpha's code; it targets an already-configured vault, which is exactly what this file deploys.

## 11. Deployment summary

Machine spec: `strategies/example-wsteth-usdc-loop-base.json`. Dry-run:

```bash
python -m deploy strategies/example-wsteth-usdc-loop-base.json
```

## 12. Sign-off

| Who | Role | Date | Decision |
|---|---|---|---|
| — | Strategist | — | not applicable (example) |
