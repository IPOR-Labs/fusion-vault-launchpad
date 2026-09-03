# 03 — The strategy file: `strategies/<name>.json` and chain contexts

The deployer reads exactly two files: the strategy JSON and the chain context it names. Nothing strategy-specific lives in code. The schema is `schema/strategy.schema.json`; the worked example is `strategies/example-usdc-aave-base.json` with its human spec `strategies/example-usdc-aave-base.md`.

## 1. Two files per strategy

| File | Purpose | Written by |
|---|---|---|
| `strategies/<name>.md` | The human spec: intent, decisions, provenance tags, sign-off. Start from `templates/strategy-spec.md`. | agent, from the human's answers |
| `strategies/<name>.json` | The machine spec the deployer executes | agent, from the `.md` |

Keep them reconciled:

```bash
python tools/spec_lint.py strategies/<name>.json     # exit 1 if the vault name/symbol in the JSON is not in the .md; warns on address mismatches
python -m deploy strategies/<name>.json               # schema + semantic checks at load, then the dry-run plan
```

## 2. Validation the loader enforces

`deploy/config.py` rejects the file at load time if:

| Rule | Error |
|---|---|
| JSON Schema violation | `jsonschema.ValidationError` |
| `fees.recipients[].split_bps` do not sum to 10000 | `split_bps must sum to 10000` |
| whitelist disabled at launch but `initial_accounts` non-empty | `whitelist disabled at launch but initial_accounts is non-empty` |
| `transferability.enabled_at_launch` without `irreversible_ack: true` | `requires irreversible_ack=true` |
| the underlying has no `price_feeds` entry | `underlying <addr> missing from price_feeds` |
| a Universal Token Swapper fuse family does not match its substrate encoding, or families are mixed on one market | `universal token swapper: …` |

Before a **live** broadcast, `deploy/guards.py` additionally refuses any configuration in which the signer, `vault.initial_owner_override`, a `roles.grants[].account`, a `whitelist.initial_accounts[]` entry or a `fees.recipients[].address` is one of anvil's ten default accounts.

## 3. Field reference

### `meta`

| Key | Type | Notes |
|---|---|---|
| `name` | `^[a-z0-9-]+$` | the slug; also the state file stem |
| `client` | string | organisation |
| `contact` | string | optional |
| `spec_version` | string | `v2` today |
| `spec_source` | string | path to the `.md` |
| `status` | `DRAFT` / `READY-FOR-REVIEW` / `APPROVED` / `SHIPPED` | |

### `chain`

| Key | Notes |
|---|---|
| `id` | numeric chain id (1, 8453, …) |
| `context` | file stem under `contexts/`. Shipped: `mainnet-ethereum-fusion`, `base-fusion`. |

### `vault`

| Key | Notes |
|---|---|
| `name`, `symbol` | ERC-20 name and symbol (symbol ≤ 11 chars) |
| `underlying` | token address; must also appear in `price_feeds` |
| `decimals` | underlying decimals (informational) |
| `total_supply_cap_underlying` | **preferred.** Human amount of the underlying, e.g. `"100000"` for 100,000 USDC. Converted at deploy using the vault's own `decimals()` (share decimals = underlying + offset). |
| `total_supply_cap` | escape hatch: raw cap in **share** units. Avoid. |
| `redemption_delay_seconds` | anti-flash-loan guard between deposit and withdraw/transfer; default 1; **immutable after clone** |
| `dao_fee_package_index` | **immutable.** Index into the factory's fee packages. Read the current packages with `getDaoFeePackage(uint256)` on the FusionFactory and confirm with the human. |
| `initial_owner_override` | the appointed **final** OWNER. `null` = the deployer. |
| `deployer_temporary_owner` | `true`: clone under the deployer so it keeps a temporary OWNER role alongside the appointed owner (useful when an agent will later run hardening steps and renounce). `false`/`null`: clone under the appointed owner only. Make it an explicit decision. |

If `deployer_temporary_owner` is `true` and `initial_owner_override` differs from the deployer, `roles.grants` **must** include an `OWNER_ROLE` grant for the appointed owner. Step `01_clone` warns otherwise.

### `roles`

| Key | Notes |
|---|---|
| `execution_delay_seconds` | default per-grant delay applied by `11_roles` (usually 0 at launch) |
| `grants[]` | `{role, account, execution_delay_seconds?}`; role names as in `Roles.sol`: `OWNER_ROLE`, `GUARDIAN_ROLE`, `ATOMIST_ROLE`, `ALPHA_ROLE`, `FUSE_MANAGER_ROLE`, `PRE_HOOKS_MANAGER_ROLE`, `CLAIM_REWARDS_ROLE`, `TRANSFER_REWARDS_ROLE`, `WHITELIST_ROLE`, `CONFIG_INSTANT_WITHDRAWAL_FUSES_ROLE`, `WITHDRAW_MANAGER_REQUEST_FEE_ROLE`, `WITHDRAW_MANAGER_WITHDRAW_FEE_ROLE`, `UPDATE_MARKETS_BALANCES_ROLE`, `UPDATE_REWARDS_BALANCE_ROLE`, `PRICE_ORACLE_MIDDLEWARE_MANAGER_ROLE` |

How grants are used: `01b_bootstrap_roles` grants **the deployer** every role listed here so one signer can run the pipeline; `11_roles` grants the listed **accounts**; `10_whitelist` handles `WHITELIST_ROLE` from `whitelist.initial_accounts`. One account holding everything is the normal trial posture. The Guardian should still be a different account from the Owner.

### `fuses[]`, `balance_fuses[]`

| Key | Notes |
|---|---|
| `fuses[].name` | resolved through the context's `fuses` map, and checked against the on-chain `FuseWhitelist` |
| `balance_fuses[].market` | market name (`AAVE_V3`, `MORPHO`, `ERC20_VAULT_BALANCE`, …) resolved via the context `markets` map or the SDK's `IporFusionMarkets` |
| `balance_fuses[].fuse` | balance fuse name from the context |

Every market you act on needs a balance fuse. Include `ERC20_VAULT_BALANCE` with `ERC20BalanceFuse` in virtually every vault. The factory injects standard fuses (`BurnRequestFeeFuse`) on its own; do not list them; `02b_standard_fuses` upgrades them to the latest version from the context's `standard_fuses`.

### `substrates[]`

`{market, encoding, values}`. Encodings (`deploy/encoders/substrates.py`):

| `encoding` | `values` items | Used for |
|---|---|---|
| `address` | `"0x…"` | Aave V3 / Spark assets, ERC-4626 vaults, tracked tokens on market 7, legacy Universal Swapper |
| `morpho_market_id` | `"0x…"` (bytes32) | Morpho Blue markets |
| `raw_bytes32` | `"0x…"` (32 bytes) | anything already encoded |
| `odos_substrate` | `{"kind":"token","address"}` / `{"kind":"slippage","slippage_bps"}` | Odos swapper |
| `universal_typed_substrate` | `{"kind":"token","address"}`, `{"kind":"target","address"}`, optional one `{"kind":"slippage",…}` | `UniversalTokenSwapperFuse` / `…EthFuse` / `…V2` |
| `universal_selector_substrate` | `{"kind":"token","address"}` for every token; `{"kind":"target","target","selector"}` per allowed `(contract, function)` | `UniversalTokenSwapperWithVerificationFuse` family |
| `euler_substrate` | `{euler_vault, is_collateral, can_borrow, sub_accounts}` | Euler V2 |
| `aave_v4_substrate` | `{spoke, reserve_id, is_collateral, can_borrow}` | Aave V4 |

The **Universal Token Swapper encoding is dictated by the fuse family, not the market**. A word in the wrong layout is silently ignored by the fuse and every swap reverts. `deploy/swapper.py` enforces name → encoding at load; `--verify-only` fails on non-canonical words.

### `dependency_graph[]`

`{from, to: [...]}`: explicit extra edges only. The deployer **derives** the mandatory edges itself: every balance-fuse market except `ERC20_VAULT_BALANCE` → `ERC20_VAULT_BALANCE`. You cannot opt out of the derived edges.

### `market_limits[]`

`{market, limit}`: per-market allocation caps. Optional at launch.

### `price_feeds[]`

`{asset, feed_type, feed?, params?}`. The underlying **must** be listed.

| `feed_type` | `feed` / `params` | Behaviour |
|---|---|---|
| `prebuilt` | `feed`: a name from the context's `price_feed_factories` | registers an already-deployed feed |
| `literal` | `feed`: an aggregator address (Chainlink-compatible `latestRoundData`) | registers that address directly |
| `DualCrossReferencePriceFeedFactory` | `params.feed_a`, `params.feed_b` | deploys `A/B × B/USD` |
| `CollateralTokenOnMorphoMarketPriceFeedFactory` | `params.morpho_oracle`, `collateral_token`, `loan_token` | prices a token off a Morpho market oracle |
| `ERC4626PriceFeedFactory` | `params` per factory | `convertToAssets × underlying` |

Feeds are registered on the **vault's own** price manager, not the shared middleware. If the vault's oracle already prices an asset through the shared middleware, the step is a no-op and shows as `plan_only` in the plan/run diff. Before using a `literal` feed, read `latestRoundData()` on it and check `updatedAt` is recent; stale aggregators exist.

### `withdraw_manager`

| Key | Notes |
|---|---|
| `window_seconds` | scheduled-withdrawal window. **`0` means instant-only**: the deployer skips the call (setting 0 on-chain reverts). |
| `request_fee_bps`, `withdraw_fee_bps` | contributions burned on the request path / instant path; `null` if unset |
| `fees_managed_by` | informational |

For an instant-only vault also add a `PauseFunctionPreHook` on `request(uint256)` `0xd845a4b3` and `requestShares(uint256)` `0xfc415e9c`.

### `fees`

| Key | Notes |
|---|---|
| `tier`, `performance_bps` | informational; the on-chain tier is `vault.dao_fee_package_index` |
| `management_supplemental_bps`, `performance_supplemental_bps` | supplemental fees on top of the DAO package, paid to `recipients` |
| `recipients[]` | `{address, split_bps}` summing to 10000 |

### `instant_withdrawal.order[]`

`{fuse, params: [{encoding, value}, …]}` in queue order. `params[0]` is the amount slot filled at runtime (pass `{"encoding":"uint256","value":0}`), followed by the fuse's remaining params. Minimum counts per family are enforced (`deploy/fuses.py`). Never put a collateralised market on the queue.

### `pre_hooks[]`

`{name, selector, params}`; names resolved via the context `pre_hooks` map.

| Hook | Selector | Params |
|---|---|---|
| `ExchangeRateValidatorPreHook` | `deposit` `0x6e553f65` | `{"threshold_bps": 200}` share-price drift guard |
| `PauseFunctionPreHook` | `request` `0xd845a4b3`, `requestShares` `0xfc415e9c` | `{}` instant-only vaults |
| `UpdateBalancesPreHook` | deposit/withdraw selectors | refresh cached balances before user actions |

### `rewards`, `whitelist`, `transferability`

| Key | Notes |
|---|---|
| `rewards.enabled` | informational today; reward fuses and claim roles are configured through `fuses` and `roles` |
| `whitelist.enabled_at_launch` | `true` recommended for the trial |
| `whitelist.initial_accounts[]` | accounts granted `WHITELIST_ROLE`; must be empty if disabled |
| `transferability.enabled_at_launch` | `true` calls `enableTransferShares()`: **irreversible** |
| `transferability.irreversible_ack` | must be `true` when enabling; set only after the human acknowledged |

### `execution`

| Key | Notes |
|---|---|
| `fork_chain_id_allowlist[]` | chain ids other than `chain.id` a `--broadcast` may target (`31337` for a bare anvil). A mismatch outside the list is a **hard fail** on broadcast. |
| `gas_multiplier` | default 1.25 |
| `state_file` | `.deploy-state/<name>.json` |
| `broadcast`, `max_priority_fee_wei`, `tx_confirmations` | informational |

## 4. Chain contexts: `contexts/<ctx>.json`

Schema: `schema/deploy-context.schema.json`.

| Key | Content |
|---|---|
| `chain_id`, `ipor_abi_deployment` | e.g. `8453`, `mainnet-base-fusion` (the folder name in `ipor-abi`) |
| `public_rpc` | read-only public endpoint used by dry-runs when `RPC_URL` is unset |
| `middleware_owner` | holder of the manager role on the shared middleware; used only to impersonate on a fork, nullable |
| `fusion_factory`, `price_oracle_middleware`, `fuse_whitelist` | proxy addresses |
| `standard_fuses[]` | factory-injected fuses, **oldest → newest** |
| `price_feed_factories{}`, `fuses{}`, `pre_hooks{}` | name → address |
| `markets{}` | market name → id overrides (else the SDK's `IporFusionMarkets`) |
| `morpho_blue`, `aave_v3_pool`, `tokens{}`, `morpho_oracles{}` | convenience |

The context is a **cache** of `ipor-abi` and the on-chain whitelist. It lags. Before relying on it for a new strategy, call `getFusesByMarketId` on the chain's `FuseWhitelist` for every market you use and add any whitelisted fuse that is missing (name it as the `ipor-abi` README does). Never substitute a different fuse because the one you need is missing from the context.

Adding a chain = adding a context file with addresses from `ipor-abi/mainnet/<deployment>/addresses.json`. Always use `…Proxy` addresses.
