# <Vault name> (<SYMBOL>) — strategy spec

> Status: `DRAFT` · Chain: `<chain>` · Machine spec: `strategies/<name>.json` · Last updated: `<YYYY-MM-DD>`
>
> Provenance tags: `[client]` = the human said so · `[agent: <source>]` = resolved by the agent from a named public source · `unverified` = could not be confirmed; say what would confirm it.

## Executive summary

*Three to five sentences: what the vault does, on which chain, with which underlying, how depositors get out, who holds control at launch. A reviewer should be able to stop here and know whether to read on.*

## 1. Intent `[client]`

- **Organisation / contact**:
- **Category**: lending optimisation / yield optimisation / loop / carry / LP / other
- **Thesis** (where the return comes from, in plain words):
- **Target size at launch** (becomes the supply cap):
- **Time horizon**: evergreen / time-limited until `<date>`

## 2. Chain and underlying `[client]`

| Item | Value | Provenance |
|---|---|---|
| Chain | | |
| Underlying token (address) | | |
| Context file | `contexts/<ctx>.json` | `[agent: repo]` |

## 3. Venues `[agent]`

One row per market the vault may touch. Confirm each fuse on the chain's `FuseWhitelist` and record the date.

| Market (id) | Fuse(s) | Balance fuse | Substrates (what exactly may be touched) | Confirmed on-chain |
|---|---|---|---|---|
| `ERC20_VAULT_BALANCE` (7) | — | `ERC20BalanceFuse` | underlying | |
| | | | | |

Dependency graph: derived (every market → idle balance). Extra edges, if any:

Callback handlers (only for fuses that make a protocol call the vault back, e.g. Morpho flash loans; `deploy/callbacks.py` lists the required pairs):

| Fuse | Handler | Sender | Signature |
|---|---|---|---|
| | | | |

## 4. Pricing `[agent]`

| Asset | Feed type | Feed / factory params | `updatedAt` checked on |
|---|---|---|---|
| underlying | | | |

## 5. Roles `[client]`

| Role | Holder (address) | EOA or multisig | Note |
|---|---|---|---|
| Owner | | | |
| Guardian | | | must differ from Owner |
| Atomist | | | |
| Alpha | | | who runs the strategy |
| Fuse manager, pre-hooks manager, oracle manager, instant-withdrawal config, fee roles, balance updaters | | | usually the operator at launch |

Ownership model: `deployer_temporary_owner` = `true` / `false`, because:

## 6. Withdrawals `[client]`

- **Mode**: instant only / scheduled / both
- **Window** (scheduled only): `<seconds>`
- **Contributions**: request `<bps>` / instant `<bps>` / none
- **Instant queue** (order, never a collateralised market):
- **Redemption delay**: `<seconds>` (default 1; immutable)

## 7. Fees `[client]`

- **DAO fee package index**: `<n>` (immutable; confirmed with `getDaoFeePackage` on `<date>`)
- **Supplemental fees**: management `<bps>`, performance `<bps>`
- **Recipients**: `<address>` `<split_bps>`

## 8. Whitelist and transferability `[client]`

- **Whitelisted at launch**: yes / no. Initial accounts:
- **Shares transferable at launch**: yes / no. If yes, the human's acknowledgement that this is one-way, in their words, dated:

## 9. Pre-hooks `[agent]`

| Hook | Selector | Params | Reason |
|---|---|---|---|
| | | | |

## 10. Risks and open items

| # | Item | Severity | Owner | Status |
|---|---|---|---|---|
| | | | | |

## 11. Deployment record

Filled in after the live broadcast; commit this section.

| Item | Value |
|---|---|
| Chain / block | |
| PlasmaVault | |
| AccessManager | |
| WithdrawManager | |
| FeeManager | |
| PriceManager | |
| Deployer | |
| Verification (`--verify-only`) date and result | |

## 12. Sign-off

| Who | Role | Date | Decision |
|---|---|---|---|
| | Strategist | | approved for fork rehearsal / approved for live deployment |
