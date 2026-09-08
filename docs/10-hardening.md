# 10 — Hardening a vault for production

A vault that passed the fork rehearsal is **verified**, not **production-ready**. During the trial one operator key holds every role, the whitelist is closed to a handful of accounts, and nothing is timelocked. Hardening is the set of changes that turn that trial posture into one a stranger's money can sit behind. This page says what those changes are, which ones this repository performs for the agent today, and which are manual.

The human acknowledges having read this page by setting `signoff.hardening_ack: true` in the strategy file. The pipeline refuses a live broadcast without it.

## 1. What "hardened" means

| Area | Trial posture (what the pipeline deploys) | Production posture |
|---|---|---|
| Owner | the deployer, or one appointed EOA | a multisig; the deployer's `OWNER_ROLE` renounced |
| Guardian | a second EOA | an independent multisig or the security team's key; can pause |
| Atomist, Fuse manager, Pre-hooks manager, oracle manager | the operator EOA | a multisig, with an execution delay on the sensitive functions |
| Alpha | the bot's EOA | the bot's EOA only; nothing else |
| Whitelist | closed, a few accounts | closed with a managed list, or opened deliberately (one-way) |
| Transferability | off | off, or on with the acknowledgement recorded (one-way) |
| Execution delays | 0 | non-zero on role grants and configuration changes (`roles.execution_delay_seconds`, per-grant `execution_delay_seconds`) |
| Fees | recipients = operator | recipients = treasury multisig |
| Instant-withdrawal queue | as rehearsed | reviewed against the production market limits |
| Market limits | none | `market_limits[]` sized to the mandate |

## 2. What this repository does for the agent

| Change | How | Status |
|---|---|---|
| Grant production roles to multisigs | edit `roles.grants[]`, re-run `--only-step 15 --broadcast …` (`11_roles`) | works today |
| Add execution delays | `roles.execution_delay_seconds` and per-grant `execution_delay_seconds`; `11_roles` applies them | works today |
| Move fee recipients | `fees.recipients[]`, re-run `07_fees` | works today |
| Set market limits | `market_limits[]` | works today |
| Whitelist changes | `whitelist.initial_accounts[]`, re-run `10_whitelist` (add only) | works today; removal is manual |
| Verify the result | `--verify-only` reads every role holder and delay back from the access manager | works today |
| **Renounce the deployer's roles** | not a pipeline step yet; `OWNER_ROLE` renounce is the last hardening action and must come after the multisig confirmed it holds OWNER | manual (`app.ipor.io` or `cast send` on the access manager) |
| Revoke a role, remove a whitelisted account, open the whitelist, enable transfers | one-way or destructive; deliberately outside the pipeline | manual, owner-signed |

Ownership model matters here. With `vault.deployer_temporary_owner: true` (Mode A) the deployer keeps a temporary `OWNER_ROLE` next to the appointed owner and can run the whole hardening list autonomously before renouncing. With Mode B every change is signed by the appointed owner.

## 3. The hardening walk

1. Rehearse the hardened configuration on a fork first: copy the strategy, replace every EOA with the production address, set the delays, and run `--broadcast --rehearse` against anvil. The rehearsal's role step and `--verify-only` prove the grants; the deposit and withdrawal prove the whitelist still admits the intended depositors.
2. Apply the same edits to the live strategy file and re-run the affected steps with `--only-step` on the live chain, after the human's "yes" for each run.
3. Confirm every role holder with `--verify-only`, then hand the human the exact renounce transaction for the deployer's roles. The human, or the multisig, sends it.
4. Record the final role table in the `.md` spec and set `meta.status` to `SHIPPED`.

## 4. Before the vault is shown to anyone

Listing a vault on the IPOR front end (`app.ipor.io`) is not something this repository or the agent can do. It requires contacting the IPOR Labs team through the channels on [docs.ipor.io](https://docs.ipor.io); the team reviews the configuration and the fuse whitelist status before listing. The human acknowledges this with `signoff.frontend_listing_ack: true`. Until then the vault is reachable only by its address, through the SDK, a block explorer, or a custom front end.
