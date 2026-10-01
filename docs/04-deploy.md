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
| 7 | `03b_callback_handlers` | `updateCallbackHandler` for every `callback_handlers[]` entry (flash-loan callbacks); skips entries the vault already routes, read from its storage | FUSE_MANAGER |
| 8 | `04_balance_fuses` | `addBalanceFuse` per market and `updateDependencyBalanceGraphs` with the **derived** graph | FUSE_MANAGER |
| 9 | `05_price_feeds` | deploys factory feeds, registers sources on the **vault's own** price manager, skips assets it already prices; refuses to finish while any asset is unpriceable | PRICE_ORACLE_MIDDLEWARE_MANAGER on the vault's manager |
| 10 | `06_withdraw_manager` | `updateWithdrawWindow`; skipped when `window_seconds == 0` | ATOMIST |
| 11 | `07_fees` | supplemental fees and recipients | fee roles |
| 12 | `08_instant_withdrawal` | `configureInstantWithdrawalFuses` in queue order | CONFIG_INSTANT_WITHDRAWAL_FUSES |
| 13 | `09_pre_hooks` | `setPreHookImplementations` | PRE_HOOKS_MANAGER |
| 14 | `10_whitelist` | grants `WHITELIST_ROLE` to `whitelist.initial_accounts` | ATOMIST |
| 15 | `11_roles` | grants every `roles.grants[]` account | role admins |
| 16 | `12_transferability` | `enableTransferShares()` if enabled at launch: irreversible | ATOMIST |

After the last step a broadcast runs the verification report (§6) and prints the deployed addresses. With `--rehearse` on a local fork it then runs the rehearsal stage (§4b): the vault is used, not only read.

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
| `--rehearse` | After a `--broadcast` on a **local fork only**: funds the deployer from `rehearsal.token_holder`, deposits, executes the rehearsal script's batches, checks the accounting after each, withdraws through the configured path. Exits non-zero if any check fails. |
| `--rehearse-only` | Runs only that stage against the vault in the recorded state (local fork, `RPC_URL` and the fork key). |
| `--from-step N` | Resume from index N. |
| `--only-step N` | Run a single step (for example re-run `05_price_feeds`). |
| `--force-restart` | Discard the state file for this strategy. Use before the **first** live broadcast if a rehearsal wrote state; **never** after an interrupted live run. |

Safety checks at session open:

- `RPC_URL` must be set for any `--broadcast`; a dry-run may fall back to the context's `public_rpc`.
- The RPC's chain id must equal `chain.id` or be listed in `execution.fork_chain_id_allowlist`; on `--broadcast` a mismatch is a **hard fail**, on a dry-run a warning.
- On a live node, any anvil default account among the signer, role holders, whitelist or fee recipients is a **hard fail**.
- A public RPC host with `--broadcast` prints a warning: use a private endpoint and run detached.
- Resume check: before a run resumes from a state file, the recorded clone transaction must be mined with success on the connected chain and the vault and its access manager must have code. Otherwise the run stops before sending anything; `--force-restart` if the state belongs to an old fork or a failed attempt, or fix `RPC_URL` if the vault should exist. A clone that was never mined (a wallet cancel, a failed send) leaves no addresses behind.
- No send without a contract: every transaction the pipeline sends, with any signer, is refused when its target has no code on the connected chain.
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

# terminal B — broadcast against the fork with anvil's default account #0, then use the vault
RPC_URL=http://127.0.0.1:8546 \
DEPLOYER_PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80 \
python -m deploy strategies/<name>.json --broadcast --rehearse
```

Expected outcome:

- all 17 steps execute;
- the verification report is green: underlying matches, fuses match (declared + standard factory-injected), window matches or "instant-only", all role grants present, **all assets price via the vault's own oracle**, **all dependency-graph edges present**, queue params satisfy every fuse family, substrate sets match and are canonical, cap correct;
- the rehearsal report is green (§4b): deposit credited 1:1, every declared fuse executed, NAV consistent after each batch, withdrawal paid;
- `.deploy-state/<name>.json` holds the `fusion_instance` and every tx hash; `.run.json` holds the actions with gas; `.rehearsal.json` holds the observations.

Then diff the plan against the run:

```bash
python tools/plan_diff.py .deploy-state/<name>.plan.json .deploy-state/<name>.run.json
```

Exit 0 means the broadcast did nothing the plan did not declare. `plan_only` entries are usually benign: an already-priceable asset skipped, or the standard-fuse upgrade found nothing to do because the factory already injects the latest version. Any `run_only` entry is a hard fail; investigate before going further. A `config_hash` mismatch between the two artifacts means the JSON changed between dry-run and rehearsal; redo the dry-run.

Notes:

- If a step fails, fix the JSON or the context and re-run the same command. Completed steps are skipped from state.
- When the rehearsal is clean, **delete `.deploy-state/<name>.json`** (it holds fork addresses) or pass `--force-restart` on the live run.
- Sum `gas_used` in `.run.json` to estimate what to fund the live deployer with. The shipped example used about 10.4M gas on a Base fork (the clone alone is about 8.9M); Ethereum gas prices make the same deployment far more expensive.

## 4a. Rehearsing as the production deployer, and preparing the fork

Two options make a fork rehearsal reproduce the live run instead of a stand-in:

- `--signer impersonate` (fork only) makes anvil sign for `DEPLOYER_ADDRESS`, so the clone owner, every role grant, the whitelist entries and the factory's per-client fee package are exactly the ones the live run will use. No key is needed. The pipeline refuses this mode against a live node.
- A rehearsal script may define `prepare(env) -> list[str]`. It runs once, before the deposit, only on the fork, for third-party state the strategy depends on but does not own: a market parameter the venue's governor has not set yet, a pool that does not exist, liquidity to borrow, a fee package the DAO still has to approve. Every returned line is printed as a warning and stored under `observations.prepare`, so nobody mistakes it for chain state. The env offers `impersonated_call(sender, to, signature, arg_types, args)`, `impersonated_send(sender, to, data)`, `deploy_contract(sender, bytecode, constructor_args)` and `advance_time(seconds)`; impersonated calls estimate gas first.

```bash
RPC_URL=http://127.0.0.1:8546 DEPLOYER_ADDRESS=0xYourDeployer \
python -m deploy strategies/<name>.json --broadcast --rehearse --signer impersonate
```

A rehearsal that needed `prepare` is only as good as the prepared assumptions; list them in the strategy's `.md` (§13) next to what still has to happen on the live chain.

## 4b. The rehearsal stage — what "verified" means

Reading configuration back proves the vault is configured. It does not prove the vault works: a granted substrate the fuse cannot act on, a market counted twice, or a withdrawal path that cannot pay all pass every read-back check. The rehearsal stage (`deploy/rehearsal.py`, rules in `deploy/rehearsal_rules.py`) uses the vault on the fork:

| Step | What happens | Fails when |
|---|---|---|
| Roles | the deployer receives `ALPHA`, `WHITELIST` and balance-updater roles for the rehearsal (fork only) | a grant reverts |
| Fund | `rehearsal.token_holder` is impersonated and sends `rehearsal.deposit_underlying` to the deployer | the holder is short |
| Deposit | `approve` + `deposit` | `totalAssets` does not rise by exactly the deposit (share rounding aside), or no shares are minted |
| Coverage | the script's batches are collected | a declared fuse is exercised by no batch (unless listed in `rehearsal.allow_unexercised`) |
| Execute | each batch is sent; then `updateMarketsBalances` on every market | NAV moves more than `rehearsal.max_nav_drift_bps` (up = a market counted twice, down = value not tracked or overpaid); cached NAV ≠ refreshed NAV (dependency graph incomplete); any market worth more than the vault |
| Unwind | the script's `unwind` batches, for leveraged vaults | same checks |
| Withdraw | instant: `withdraw(min(half the deposit, maxWithdraw))`; scheduled: `requestShares` → +1h → `releaseFunds(timestamp, shares)` by the Alpha → `redeemFromRequest` | the payout is short, or NAV does not drop by the payout |

The batches come from `rehearsal.script`, a Python file built on the SDK fuse wrappers (`rehearsals/<name>.py`; copy the nearest SDK walk from `ipor-fusion.py/tests/test_simulate_*.py`). The stage never runs against a live chain: it impersonates accounts and moves time.

Re-running the stage on a used fork vault accumulates deposits; for a clean claim, `make clean-state`, restart anvil, and broadcast again.

## 5. Live broadcast

Pre-flight, every item:

- [ ] Fork rehearsal clean and `plan_diff` exit 0.
- [ ] `vault.initial_owner_override` is the real appointed owner; `vault.deployer_temporary_owner` is an explicit decision.
- [ ] Every `roles.grants[].account`, `whitelist.initial_accounts[]` and `fees.recipients[].address` is a production address. (The pipeline checks for anvil defaults; it cannot check for typos.)
- [ ] `whitelist.initial_accounts` contains the first depositor.
- [ ] `RPC_URL` in `.env` is a **private** endpoint for the target chain.
- [ ] `DEPLOYER_PRIVATE_KEY` is in `.env` and the account is funded on the target chain.
- [ ] The state file for this strategy is gone or `--force-restart` is passed.
- [ ] `signoff.roles_reviewed`, `signoff.hardening_ack` and `signoff.frontend_listing_ack` are `true`, each set after the human's own words (`docs/05-human-in-the-loop.md` §4).
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
6. Hand the vault to the Alpha. Operating it (executing fuse actions, simulating a step before sending it) is the SDK's job, not this repository's; the pointers are in `docs/07-resources.md` §"After deployment".

## 5b. Signing with a browser wallet

`--signer browser` replaces the private key with a wallet in your browser (Rabby, MetaMask, any EIP-6963 wallet):

```bash
DEPLOYER_ADDRESS=0xYourDeployer python -m deploy strategies/<name>.json --broadcast --i-understand-this-is-live --signer browser
# or: make sign STRATEGY=strategies/<name>.json RPC_URL=<your private RPC> DEPLOYER_ADDRESS=0xYourDeployer
```

1. Before the first transaction the pipeline loads the dry-run plan for this exact strategy file (`.deploy-state/<name>.plan.json`); when it is missing or stale it runs the dry-run first. The page therefore lists **every signature of the run, per step**, before you sign anything.
2. It starts a local page on `http://127.0.0.1:8789` (`--signer-port` changes it) and waits until a wallet connects with the right chain and, when `DEPLOYER_ADDRESS` is set, the right account. After connecting, the page shows a pre-flight: the deployer's gas balance and whether the factory holds a custom DAO fee package for this deployer. The package is fixed when the vault is cloned, so a missing custom package has to be fixed before the first signature.
   Before any wallet can connect, the page asks you to accept a short disclaimer: the page is provided as is, with no guarantee that it is fully reliable, and your wallet is the source of truth for what you sign. The pipeline refuses a wallet connection without it and logs the time of acceptance.
3. The top of the page is a map of the vaults being deployed (one card for a single vault; the whole set and its connections with `deploy.campaign`, §5c). The step list on the right shows the stage being signed: done steps are filled, the step marked **You are here** is waiting for you, the rest are ahead. Click any finished step or vault to review its transactions (hashes, gas, arguments); a *Back to "You are here"* button and a banner bring you back.
4. The page moves on by itself: after you connect and after every signature it opens the next transaction as soon as the pipeline has prepared it, so each signature is one click on **Sign**. A status bar under the header says what is happening (preparing the transaction, waiting for the wallet, waiting for the block, verifying) with an elapsed-time counter, so a slow RPC never looks like a frozen page.
5. Every step builds its transaction as with a key (gas estimate, revert check), then hands the unsigned transaction to the page: contract (named: FusionFactory, PlasmaVault, AccessManager …), function, arguments, gas limit and raw calldata. Confirm it in the wallet; nonce and fees are set by the wallet. The page never offers the same transaction twice. After a page reload, the signature card offers to reconnect the wallet in place.
6. The page returns the hash; the pipeline waits for the receipt, records state and moves on. Rejecting in the wallet or pressing *Reject* on the page sends nothing and does not stop the run: the transaction keeps waiting and *Sign* asks the wallet again. Stop the run with Ctrl+C in the terminal; re-running the same command resumes, and steps finished by an earlier run appear as *signed earlier*.
7. After the last step the page shows the verification report and the deployed contracts, and keeps serving for `--signer-linger` minutes (default 30) so the record stays browsable. *Close signing session* stops it.

**Roles.** The base setup (step `01b`) gives the deployer ATOMIST and FUSE_MANAGER, plus a role only when a configured step needs it: PRICE_ORACLE_MIDDLEWARE_MANAGER for price feeds, CONFIG_INSTANT_WITHDRAWAL_FUSES for an instant-withdrawal order, PRE_HOOKS_MANAGER for pre-hooks (`deploy/role_plan.py`). In the final roles step (`11`), OWNER, ATOMIST and FUSE_MANAGER grants are required; every other grant is optional and has a **Skip** button on the page (mark a grant `"required": true` in `roles.grants` to make it mandatory). Skipped grants are recorded in the state file and reported by the verification as missing, not failed. The alpha roles (ALPHA, UPDATE_MARKETS_BALANCES, UPDATE_REWARDS_BALANCE, CLAIM_REWARDS, TRANSFER_REWARDS) are not granted by the base run at all; once the alpha exists, grant the ones listed in `roles.grants` with `python -m deploy <strategy> --broadcast --signer browser --assign-alpha` (`make assign-alpha`), which runs only that grant step on the deployed vault.

**On a fork.** The page works against an anvil fork too, which is the way to rehearse the signing itself. Point the wallet's network at the fork first (Rabby: More → Modify RPC URL; MetaMask: Networks → Edit → add the RPC URL); the page shows the fork's URL and reminds you. A fork has the live chain's id, so the chain id cannot show where the wallet sends. The pipeline therefore funds a random marker account on the fork only, and the page reads its balance through the wallet's RPC when you connect and again before every signature: a wallet still on the live RPC is refused and nothing reaches it. Fund the deployer on the fork with `cast rpc anvil_setBalance <deployer> 0x56BC75E2D63100000 --rpc-url <fork>`. The rehearsal stage (`--rehearse`) still runs with `--signer impersonate` or the fork key: it impersonates accounts and moves time, which a wallet cannot do.

The page is `tools/browser_signer/index.html`: plain HTML, no build step, Fusion app design tokens, wallet discovery through EIP-6963 (falls back to `window.ethereum`). It binds to 127.0.0.1 only, refuses requests addressed to any other host name, and every POST carries a per-run token embedded in the page, so another site open in the browser cannot answer for you. Nothing leaves the machine except the transactions you confirm. `DEPLOYER_PRIVATE_KEY` is ignored in this mode.

## 5c. Deploying a set of connected vaults

A strategy is sometimes several vaults: an index vault that holds shares of per-asset vaults, which in turn hold other vaults. `deploy.campaign` deploys the whole set in one run from a small campaign file:

```json
{
  "name": "my-vault-set",
  "vaults": [
    {"id": "leaf",   "strategy": "leaf.json"},
    {"id": "parent", "strategy": "parent.json"},
    {"id": "remote", "strategy": "remote.json", "blocked": "why it cannot be deployed yet"}
  ],
  "links": [
    {"parent": "parent", "child": "leaf", "kind": "erc4626", "placeholder": "0x<stand-in ERC4626 in parent.json>"},
    {"parent": "parent", "child": "remote", "kind": "cross-chain", "blocked": "no cross-chain fuse yet"}
  ]
}
```

```bash
python -m deploy.campaign my-vault-set.campaign.json                                   # dry-run every vault
DEPLOYER_ADDRESS=0x… python -m deploy.campaign my-vault-set.campaign.json --broadcast --signer browser
# or: make sign-set CAMPAIGN=my-vault-set.campaign.json RPC_URL=… DEPLOYER_ADDRESS=0x…
```

- **Order.** Children deploy before their parents; the page numbers the vaults in that order.
- **Stand-ins.** A parent's strategy is rehearsed alone against a stand-in ERC4626 (any vault with the same asset). Before the parent deploys, every occurrence of the link's `placeholder` in its strategy (substrates, price feeds, instant-withdrawal queue) is replaced by the child's deployed vault address; the resolved file is written to `.deploy-state/<campaign>/<vault>.resolved.json`.
- **Connections.** Right after a parent deploys, each `erc4626` link is one signature on the child: its access manager grants the parent `WHITELIST_ROLE`, so the parent may deposit. Already granted means nothing is sent. `cross-chain` links are drawn only.
- **Blocked.** A vault marked `blocked`, or a parent that holds a blocked child through an `erc4626` link, is skipped and shown as blocked with its reason. A failed stage stops the run; everything after it is marked blocked. Re-running resumes: finished steps are skipped.
- **Chains.** `RPC_URL_<chainId>` overrides `RPC_URL` per chain for a set that spans chains.
- **Page.** The top of the page becomes a map of the vaults and connections (status, order, signatures, a "You are here" marker). The right-hand list shows the steps of the stage being signed; click a finished vault or connection on the map to review it.

Keep campaign files next to the strategies they reference (outside this repository for real clients, like the strategies themselves).

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
| **every `callback_handlers[]` entry registered, read from vault storage** | the first flash loan reverts `HandlerNotFound()` |
| instant-queue params long enough for each fuse family | instant withdrawals through that entry revert |
| substrate sets: expected ⊆ on-chain, every word canonical for typed encodings | the fuse ignores the word; the venue is dead |
| total supply cap in underlying terms | cap off by the decimals offset |

The three bold rows are the **critical hazards**. They are read from the vault's own contracts, never from the shared middleware or the JSON. The rehearsal stage (§4b) adds the behavioural checks: deposit credited, every fuse executed, no double accounting, withdrawal paid.

## 7. Recovery and re-runs

| Situation | Do |
|---|---|
| A step failed on the fork | fix, re-run the same command; state skips completed steps |
| A step failed on a live chain | read `.deploy-state/<name>.json`, confirm every recorded tx receipt on-chain, fix the cause, re-run `--broadcast --i-understand-this-is-live` **without** `--force-restart` |
| The live run was interrupted (timeout, network) | same as above; never restart from scratch, the vault is already cloned |
| Someone changed configuration by hand on-chain | update the JSON to match, then reconcile the state file's `config_hash` (`deploy/state.py::hash_config`) instead of discarding state |
| Need to redo one step | `--only-step N --broadcast …` |
| Wrong chain by accident | the chain-id guard stops it; fix `RPC_URL` |
