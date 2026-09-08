# Example USDC Lending (exUSDC) — strategy spec

> **This is a worked example, not a real strategy.** Every address that holds a role, receives fees or is whitelisted is an anvil default test account whose private key is public. The pipeline refuses to broadcast this file to a live chain (`deploy/guards.py`). Copy it, rename it, and replace every placeholder before you deploy anything real.

## Executive summary

A single-venue USDC vault on Base that supplies idle USDC to Aave V3 and keeps the rest idle in the vault. Instant withdrawals only (no scheduled window), whitelisted and non-transferable at launch, DAO fee tier B. Roles are held by one operator account during the trial with a separate Guardian and a separate Alpha.

## 1. Intent `[client]`

- **Organisation**: Example Co
- **Category**: lending optimisation (single venue in this example)
- **Thesis**: earn the Aave V3 USDC supply rate on Base with no leverage and no swaps.
- **Target TVL at launch**: 100,000 USDC (enforced as the supply cap)

## 2. Chain and underlying `[client]`

| Item | Value | Provenance |
|---|---|---|
| Chain | Base (8453) | `[client]` |
| Underlying | USDC `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` | `[client]` |
| Context | `contexts/base-fusion.json` | `[agent: repo]` |

## 3. Venues `[agent]`

| Market | Fuse | Balance fuse | Substrates |
|---|---|---|---|
| `AAVE_V3` (1) | `AaveV3SupplyFuse` | `AaveV3BalanceFuse` | USDC |
| `ERC20_VAULT_BALANCE` (7) | — | `ERC20BalanceFuse` | USDC |

The dependency graph edge `AAVE_V3 → ERC20_VAULT_BALANCE` is derived by the deployer; nothing to declare.

## 4. Pricing `[agent]`

USDC is priced through the Chainlink USDC/USD aggregator on Base, `0x7e860098F58bBFC8648a4311b374B1D669a2bc6B`, registered on the vault's own price manager as a `literal` feed. If the vault's oracle can already price USDC through the shared middleware, the deployer skips the registration.

## 5. Roles `[client]`

| Role | Holder | Note |
|---|---|---|
| Owner, Atomist, Fuse manager, Pre-hooks manager, Whitelist, instant-withdrawal config, fee roles, oracle manager | `0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266` | anvil account #0 — **placeholder** |
| Guardian | `0x3C44CdDdB6a900fa2b585dd299e03d18d4beD2e2` | anvil account #2 — **placeholder**; must be independent of the Owner |
| Alpha, balance updater, rewards balance updater | `0x70997970C51812dc3A010C7d01b50e0d17dc79C8` | anvil account #1 — **placeholder**; the bot or person that runs the strategy |

Ownership model: Mode B (`deployer_temporary_owner: false`), the appointed owner is the clone owner.

## 6. Withdrawals `[client]`

Instant only: `window_seconds: 0`, the request path is paused with `PauseFunctionPreHook` on `request(uint256)` and `requestShares(uint256)`. Instant queue: Aave V3 USDC.

## 7. Fees `[client]`

DAO tier B (`dao_fee_package_index: 1`). No supplemental fees. Recipient of any future supplemental fee: the operator account above.

## 8. Whitelist and transferability `[client]`

Whitelisted at launch (initial account: the operator). Shares non-transferable at launch. Both are one-way switches later; launching closed keeps every option open.

## 9. Pre-hooks `[agent]`

`ExchangeRateValidatorPreHook` on `deposit` with a 200 bps threshold, plus the two pause hooks from section 6.

## 10. Deployment summary

Machine spec: `strategies/example-usdc-aave-base.json`. Dry-run:

```bash
python -m deploy strategies/example-usdc-aave-base.json
```

## 11. Sign-off

| Who | Role | Date | Decision |
|---|---|---|---|
| — | Strategist | — | not applicable (example) |

| Acknowledgement | Flag | The human's words | Date |
|---|---|---|---|
| Reviewed which address holds which role | `signoff.roles_reviewed` | not applicable (example; all placeholders) | — |
| Hardening before production, with this repository's help | `signoff.hardening_ack` | not applicable (example) | — |
| Front-end listing requires the IPOR Labs team | `signoff.frontend_listing_ack` | not applicable (example) | — |

## 12. Rehearsal `[agent]`

Script `rehearsals/example-usdc-aave-base.py`: supply the deposited USDC to Aave V3 (the SDK guide walk). Fork rehearsal on 2026-09-08: 10,000 USDC deposited from the Morpho Blue contract's balance, credited 1:1; Aave supply moved NAV +0.1 bps; the instant queue paid a 5,000 USDC withdrawal out of Aave.
