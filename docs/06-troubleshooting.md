# 06 — Troubleshooting

Symptoms first, then cause and fix.

## Setup and environment

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: ipor_fusion` | SDK not installed in the active venv | `source .venv/bin/activate && pip install -r requirements-dev.txt` |
| `ModuleNotFoundError: ipor_fusion.core.fusion_factory` | an old SDK version | `pip install -U "ipor-fusion>=3.1"`; the pinned version in `requirements.txt` is known to work |
| `RPC not reachable: …` | wrong `RPC_URL`, anvil not running, wrong port | `lsof -i :<port>`, `cast chain-id --rpc-url …` |
| `RPC_URL not set — a --broadcast needs an explicit endpoint` | broadcast without `RPC_URL` | set it to the anvil URL (rehearsal) or your private RPC (live) |
| `DEPLOYER_PRIVATE_KEY not set — required for --broadcast` | no key in `.env` or the shell | the human adds it to `.env`; simulation does not need it |
| `chain_id mismatch on --broadcast: live RPC reports 1, strategy targets 8453` | RPC points at the wrong chain, or a bare anvil id is not allow-listed | fix `RPC_URL`, or add the id to `execution.fork_chain_id_allowlist` |
| `Broadcast against a LIVE node … requires --i-understand-this-is-live` | the node is not anvil/hardhat | for a rehearsal, point `RPC_URL` at anvil; for a live run, pass the flag after the human's yes |
| `Refusing to broadcast to a live chain: … well-known test account` | placeholder addresses left in the file | replace every anvil default account with real addresses |
| A public RPC returns 403 or rate-limits | provider policy | use another public endpoint for reads, a private one for writes |
| First tx on the fork fails: `Fork Error: … 403 … Archive requests require a personal token` | the fork source refuses archive reads | restart anvil with `--fork-url https://mainnet.base.org` (Base) or a private RPC |
| `plan_diff` reports every `01b_bootstrap_roles` grant as `run_only` | the dry-run and the rehearsal ran with a different signer | harmless in older versions; fixed by excluding the signer from the action identity. Update the repo. |

## Strategy JSON

| Symptom | Cause | Fix |
|---|---|---|
| `FileNotFoundError: …/contexts/<name>.json` | `chain.context` does not match a file stem | shipped: `mainnet-ethereum-fusion`, `base-fusion` |
| `fuse '<Name>' missing from context '<ctx>'` | the context cache lags the on-chain whitelist, or a misspelling | confirm with `getFusesByMarketId`; add the fuse to the context; do **not** substitute another fuse |
| `underlying <addr> missing from price_feeds` | loader rule | add a `prebuilt` / `literal` / factory entry for the underlying |
| `fees.recipients split_bps must sum to 10000` | arithmetic | fix the splits |
| `transferability.enabled_at_launch requires irreversible_ack=true` | safety rule | set the ack only after the human accepted irreversibility |
| `universal token swapper: …` | fuse family ≠ substrate encoding, or mixed families on one market | one family per market; typed → `universal_typed_substrate`, WithVerification → `universal_selector_substrate`, legacy → `address` |
| `callback handlers: MorphoFlashLoanFuse needs callback_handlers entry …` | a flash-loan fuse is declared without the handler that routes its callback | add the entry the message spells out (`docs/03-strategy-json.md` → `callback_handlers[]`) and make sure the context has the handler address |
| `callback handler '<Name>' missing from context` | the context has no `callback_handlers` map, or the name differs from `ipor-abi` | add it from `mainnet/<deployment>/addresses.json` |
| `spec-lint … ❌ vault.name … not found in markdown` | the `.md` and `.json` drifted | align name and symbol in both |
| `instant-withdraw queue params too short for fuse family` | missing params in `instant_withdrawal.order[]` | see `deploy/fuses.py::QUEUE_MIN_PARAMS` |

## Deploy pipeline

| Symptom | Cause | Fix |
|---|---|---|
| `Config drift detected (state=… current=…)` | the JSON changed after a run wrote state | if nothing real was broadcast: `--force-restart`; if it was: revert the JSON to what deployed, or reconcile the hash deliberately |
| `01_clone already cloned at … — skipping` on a first live run | stale state from a rehearsal | delete `.deploy-state/<name>.json` or pass `--force-restart` **before** the first live broadcast |
| Every step "succeeds" instantly, nothing on-chain | same as above | same fix; then verify the vault actually exists at the recorded address |
| `01b: failed to grant […]` | role admin not yet held by the deployer (RPC propagation) | re-run with `--from-step 2 --broadcast …` |
| `05_price_feeds` `CRITICAL HAZARD: assets unpriceable via vault oracle` | a valued asset has no source on the vault's own oracle and no fallback | fix the `price_feeds` entry (check the feed is live with `latestRoundData()`), re-run `--only-step 9` |
| `WithdrawWindowLengthCannotBeZero` | attempted to set window 0 | leave `window_seconds: 0`; the deployer skips the call for instant-only |
| Verification: `MISSING dependency-graph edges` | graph written incompletely | re-run `--only-step 8` |
| Verification: `MISSING callback handlers`, or the Alpha's first flash loan reverts `HandlerNotFound()` `0x4bf4de4e` | `updateCallbackHandler` was never sent for that `(sender, selector)` | add / fix the `callback_handlers[]` entry, re-run `--only-step 7 --broadcast …`, then `--verify-only` |
| Verification: `NON-CANONICAL … substrate` | words in a layout the fuse does not read | re-encode with the right encoding; re-grant |
| Verification: `cap MISMATCH` | cap set in underlying decimals | use `total_supply_cap_underlying`; re-run `--only-step 3` |
| Verification: `unexpected fuses on-chain` | a fuse present that is neither declared nor standard | investigate; if it is a newer standard fuse, add it to the context's `standard_fuses` |
| `plan_diff` reports `run_only` | the broadcast did something the plan did not declare | stop; compare artifacts; usually a config change between dry-run and broadcast |
| `nonce too low` / stalled confirmations on a live chain | public RPC | private RPC; run detached; resume **without** `--force-restart` |

## Data

| Symptom | Cause | Fix |
|---|---|---|
| A `literal` feed prices but `updatedAt` is months old | stale or deprecated aggregator | pick the current aggregator from the oracle provider's official address list and verify `updatedAt` on-chain |
| A context address contradicts `ipor-abi` | the context is stale | `ipor-abi` wins; propose the context update with the source |
