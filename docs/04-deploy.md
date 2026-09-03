# 04 — Deploying: dry-run, fork rehearsal, live broadcast

`python -m deploy` turns `strategies/<name>.json` into a configured PlasmaVault. It is generic (no strategy-specific code), idempotent (completed steps are skipped from a state file), resumable, and produces machine-readable artifacts that can be diffed.

## 1. The pipeline

Steps run in this order. The **index** is what `--from-step` and `--only-step` take.

| Idx | Step | What it does | Signer needs |
|---|---|---|---|
| 0 | `00_dry_run_report` | prints the plan header | — |
| 1 | `01_clone` | `FusionFactory.clone(...)` → vault, access manager, fee/rewards/withdraw/context/price managers. Previews the deterministic addresses via `eth_call` first. | gas |
| 2 | `01b_bootstrap_roles` | grants the **deployer** every role listed in `roles.grants` so one signer can run the pipeline | OWNER (from clone) |
| 3 | `01c_total_supply_cap` | sets the cap from `total_supply_cap_underlying` using the vault's own `decimals()` | ATOMIST |
| 4 | `02_add_fuses` | `addFuses` for every `fuses[].name`, gated on the on-chain `FuseWhitelist` | FUSE_MANAGER |
| 5 | `02b_standard_fuses` | upgrades factory-injected standard fuses to the newest in the context | FUSE_MANAGER |
| 6 | `03_grant_substrates` | `grantMarketSubstrates` per market with the declared encoding | FUSE_MANAGER / ATOMIST |
| 7 | `04_balance_fuses` | `addBalanceFuse` per market and `updateDependencyBalanceGraphs` with the **derived** graph | FUSE_MANAGER |
| 8 | `05_price_feeds` | deploys factory feeds, registers sources on the **vault's own** price manager, skips assets it already prices; refuses to finish while any asset is unpriceable | PRICE_ORACLE_MIDDLEWARE_MANAGER on the vault's manager |
| 9 | `06_withdraw_manager` | `updateWithdrawWindow`; skipped when `window_seconds == 0` | ATOMIST |
| 10 | `07_fees` | supplemental fees and recipients | fee roles |
| 11 | `08_instant_withdrawal` | `configureInstantWithdrawalFuses` in queue order | CONFIG_INSTANT_WITHDRAWAL_FUSES |
| 12 | `09_pre_hooks` | `setPreHookImplementations` | PRE_HOOKS_MANAGER |
| 13 | `10_whitelist` | grants `WHITELIST_ROLE` to `whitelist.initial_accounts` | ATOMIST |
| 14 | `11_roles` | grants every `roles.grants[]` account | role admins |
| 15 | `12_transferability` | `enableTransferShares()` if enabled at launch: irreversible | ATOMIST |

After the last step a broadcast runs the verification report (§6) and prints the deployed addresses.

## 2. Command reference

```
python -m deploy <strategy.json> [flags]
```

| Flag | Effect |
|---|---|
| *(none)* | **Dry-run.** Loads and validates, previews the clone, prints every intended call with decoded args, writes `.deploy-state/<name>.plan.json`. No signer, no state written, nothing sent. |
| `--broadcast` | Sends transactions. Requires `RPC_URL` and `DEPLOYER_PRIVATE_KEY`. Writes `.deploy-state/<name>.json` after every step and `.run.json` at the end. |
| `--i-understand-this-is-live` | Required when the node is **not** a local fork (anvil, hardhat). Applies to every live chain, not only Ethereum. |
| `--verify-only` | Reads the chain against the JSON using the recorded `fusion_instance`. Needs a state file. Re-runnable at any time. |
| `--from-step N` | Resume from index N. |
| `--only-step N` | Run a single step (for example re-run `05_price_feeds`). |
| `--force-restart` | Discard the state file for this strategy. Use before the **first** live broadcast if a rehearsal wrote state; **never** after an interrupted live run. |

Safety checks at session open:

- `RPC_URL` must be set for any `--broadcast`; a dry-run may fall back to the context's `public_rpc`.
- The RPC's chain id must equal `chain.id` or be listed in `execution.fork_chain_id_allowlist`; on `--broadcast` a mismatch is a **hard fail**, on a dry-run a warning.
- On a live node, any anvil default account among the signer, role holders, whitelist or fee recipients is a **hard fail**.
- A public RPC host with `--broadcast` prints a warning: use a private endpoint and run detached.
- Config drift: if the state file's `config_hash` differs from the current JSON, the run aborts. Reconcile the JSON with what was deployed, or `--force-restart` if nothing real was broadcast.

## 3. Dry-run (simulation)

```bash
python -m deploy strategies/<name>.json
```

Needs no key and no `.env`. Read the output end to end. Every step prints what it would call: fuse addresses, substrate words, feed params, role grants, window, fees, queue. Anything that looks off is a JSON or context error; fix and re-run.

Output: `.deploy-state/<name>.plan.json`, a `manifest` (chain, RPC source, signer, gas multiplier, `config_hash`, SDK version) and an ordered `actions[]` list with `(step, action, key, args)`.

Limits of a dry-run: it reads the live chain for whitelist status and address previews, but the vault does not exist, so nothing can be verified on-chain and factory-minted feed addresses are unknown. That is what the fork rehearsal is for.

## 4. Fork rehearsal — mandatory before any live broadcast

```bash
# terminal A — fresh fork of the target chain
lsof -ti :8546 | xargs -r kill
anvil --fork-url <rpc-of-target-chain> --port 8546

# terminal B — broadcast against the fork with anvil's default account #0
RPC_URL=http://127.0.0.1:8546 \
DEPLOYER_PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80 \
python -m deploy strategies/<name>.json --broadcast
```

Expected outcome:

- all 16 steps execute;
- the verification report is green: underlying matches, fuses match (declared + standard factory-injected), window matches or "instant-only", all role grants present, **all assets price via the vault's own oracle**, **all dependency-graph edges present**, queue params satisfy every fuse family, substrate sets match and are canonical, cap correct;
- `.deploy-state/<name>.json` holds the `fusion_instance` and every tx hash; `.run.json` holds the actions with gas.

Then diff the plan against the run:

```bash
python tools/plan_diff.py .deploy-state/<name>.plan.json .deploy-state/<name>.run.json
```

Exit 0 means the broadcast did nothing the plan did not declare. `plan_only` entries are usually benign (an already-priceable asset skipped). Any `run_only` entry is a hard fail; investigate before going further. A `config_hash` mismatch between the two artifacts means the JSON changed between dry-run and rehearsal; redo the dry-run.

Notes:

- If a step fails, fix the JSON or the context and re-run the same command. Completed steps are skipped from state.
- When the rehearsal is clean, **delete `.deploy-state/<name>.json`** (it holds fork addresses) or pass `--force-restart` on the live run.
- Sum `gas_used` in `.run.json` to estimate what to fund the live deployer with; Ethereum needs far more than Base.

## 5. Live broadcast

Pre-flight, every item:

- [ ] Fork rehearsal clean and `plan_diff` exit 0.
- [ ] `vault.initial_owner_override` is the real appointed owner; `vault.deployer_temporary_owner` is an explicit decision.
- [ ] Every `roles.grants[].account`, `whitelist.initial_accounts[]` and `fees.recipients[].address` is a production address. (The pipeline checks for anvil defaults; it cannot check for typos.)
- [ ] `whitelist.initial_accounts` contains the first depositor.
- [ ] `RPC_URL` in `.env` is a **private** endpoint for the target chain.
- [ ] `DEPLOYER_PRIVATE_KEY` is in `.env` and the account is funded on the target chain.
- [ ] The state file for this strategy is gone or `--force-restart` is passed.
- [ ] **The human has said "yes" to this exact file in this session.**

Broadcast, detached, with logs:

```bash
mkdir -p .deploy-state
nohup python -m deploy strategies/<name>.json --broadcast --i-understand-this-is-live --force-restart \
  > .deploy-state/<name>.broadcast.log 2>&1 &
tail -f .deploy-state/<name>.broadcast.log
```

Post-broadcast:

1. Read the verification report at the end of the log.
2. `python -m deploy strategies/<name>.json --verify-only`; repeat any time.
3. `python tools/plan_diff.py .deploy-state/<name>.plan.json .deploy-state/<name>.run.json`.
4. Copy the deployed addresses (vault, access manager, withdraw manager, fee manager, price manager) into the `.md` spec and commit the `.md` and `.json`.
5. Make the first deposit from a whitelisted account and confirm `totalAssets()` moves.

## 6. What the verification report checks

`deploy/verification.py`, run at the end of every broadcast and by `--verify-only`:

| Check | Fail means |
|---|---|
| underlying matches | wrong vault |
| fuses: declared ⊆ on-chain; standard factory fuses recognised; anything else warned | a declared fuse missing, or an unexpected fuse present |
| withdraw window | mismatch (instant-only = expected factory default) |
| every `roles.grants` present | a grant failed or RPC lag |
| **every `price_feeds` asset returns a price from the vault's own oracle** | **the vault is broken** (deposits, withdrawals and accounting revert) |
| **every derived dependency-graph edge present on-chain** | accounting does not cascade to the idle balance |
| instant-queue params long enough for each fuse family | instant withdrawals through that entry revert |
| substrate sets: expected ⊆ on-chain, every word canonical for typed encodings | the fuse ignores the word; the venue is dead |
| total supply cap in underlying terms | cap off by the decimals offset |

The two bold rows are the **critical hazards**. They are read from the vault's own contracts, never from the shared middleware or the JSON.

## 7. Recovery and re-runs

| Situation | Do |
|---|---|
| A step failed on the fork | fix, re-run the same command; state skips completed steps |
| A step failed on a live chain | read `.deploy-state/<name>.json`, confirm every recorded tx receipt on-chain, fix the cause, re-run `--broadcast --i-understand-this-is-live` **without** `--force-restart` |
| The live run was interrupted (timeout, network) | same as above; never restart from scratch, the vault is already cloned |
| Someone changed configuration by hand on-chain | update the JSON to match, then reconcile the state file's `config_hash` (`deploy/state.py::hash_config`) instead of discarding state |
| Need to redo one step | `--only-step N --broadcast …` |
| Wrong chain by accident | the chain-id guard stops it; fix `RPC_URL` |
