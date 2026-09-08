# 05 — Human in the loop

This page lists every point where the agent must stop and get something from the human, and why. It is written for both sides.

The rule of thumb: **the agent resolves anything that can be read from code, a public API or the chain; the human decides intent, addresses, money and every irreversible action.**

## 1. The private key

The single most important boundary in this repository.

| Fact | Consequence |
|---|---|
| A dry-run needs no key. | The agent can validate, preview addresses and print the full plan at any time, and should do so before asking for anything. |
| A fork rehearsal uses anvil's public test key. | The agent runs rehearsals on its own; a real key must never be used against a fork. |
| Creating a vault on a live chain needs the human's funded key in `.env`. | The agent asks the human to add `DEPLOYER_PRIVATE_KEY` and `RPC_URL` to `.env` themselves, waits, and never asks for the key in the conversation. |
| Whoever holds the key holds every role during the trial. | The human should treat the deployer account as the vault's admin until roles are moved to production addresses. |
| Keys pasted into a chat are exposed. | If it happens, the agent says so and recommends rotating the key after deployment. |

The pipeline reads the key from the environment only, uses it only to sign the transactions it prints, and never writes it anywhere.

## 2. The autonomy policy

| Agent may do alone | Human must do |
|---|---|
| Drive the intake, write and update the human spec | Answer the `[client]` items below |
| Resolve fuse and market availability, feed choices, dependency graph, queue params | Confirm chain, underlying, venues, target size, fee tier |
| Write the JSON, run `spec_lint`, run dry-runs | Provide every address and say which are multisigs |
| Start anvil, rehearse on the fork (`--broadcast --rehearse`), run `plan_diff` | Put the key and private RPC into `.env`; fund the account |
| Run `--verify-only` | Say **"yes, deploy to <chain>"** for this exact file, in this session |
| Resume an interrupted live run after confirming receipts | Approve the resumed run if any recorded tx is missing on-chain |

Approval is per file and per session. A "yes" for a previous version of the JSON does not carry over after an edit.

## 3. Intake: the `[client]` items

| Topic | The human decides | Why the agent cannot |
|---|---|---|
| Identity | organisation, working-name slug, contact | identity |
| Strategy | category, which protocols, which markets (or a pick from the agent's enumerated list), asset pairs, the thesis | intent and risk appetite |
| Underlying | the single underlying asset | product decision |
| Size | target TVL at launch, expressed as the supply cap | risk |
| Naming | vault name and symbol | brand |
| Roles | an address for each role, at minimum Owner, Guardian, Atomist, Alpha; whether the deployer keeps a temporary owner role | custody |
| Withdrawals | instant only, scheduled, or both; window length; request and withdraw contributions | liquidity policy |
| Redemption delay | the value (default 1 second); immutable | composability trade-off |
| Fees | DAO fee package (immutable), supplemental fees, recipients | revenue |
| Whitelist | open or closed at launch (closed recommended) | compliance |
| Transferability | transferable or not at launch (not recommended); explicit acknowledgement that enabling is one-way | compliance |
| Deployment decision | dry-run only, fork rehearsal, or live | money |
| Rehearsal funding | which token holder to impersonate on the fork and how much to deposit (`rehearsal.token_holder`, `deposit_underlying`); the agent proposes a holder it read from the chain | sizing to the venue's liquidity |

If the human asks the agent to pick for them, the agent may recommend and explain, but the answer is still recorded as `[client]` with a note that it was a recommendation the human accepted.

## 4. Deployment gates

| Gate | Human provides | Agent must have shown first |
|---|---|---|
| Machine spec review | agreement that the JSON matches the human spec | `spec_lint` output, the dry-run plan |
| Key and RPC | `.env` filled in and the account funded | the gas estimate from the rehearsal |
| **Sign-off: roles** | "I have reviewed which address holds which role" → `signoff.roles_reviewed: true` | the roles table of the `.md` spec, with each holder marked EOA or multisig |
| **Sign-off: hardening** | "I know the vault must be hardened before production and that this repository helps the agent do it" → `signoff.hardening_ack: true` | `docs/10-hardening.md` |
| **Sign-off: listing** | "I know that showing the vault on app.ipor.io requires contacting the IPOR Labs team" → `signoff.frontend_listing_ack: true` | `docs/10-hardening.md` §4 |
| **Live broadcast** | an explicit "yes" for this file in this session | fork rehearsal green **including the rehearsal stage**, `plan_diff` exit 0, the three sign-offs, the pre-flight list in `04-deploy.md` §5 |
| Interrupted run | approval to resume if any recorded tx is missing on-chain | the state file and receipts |

## 5. How the agent should ask

- One question per turn. State the recommended default and mark it "(Recommended)". Always allow a custom value.
- Give the reason in one or two sentences.
- Echo the answer back before persisting anything that is hard to undo.
- For the one irreversible setting this pipeline can flip (share transferability at launch), show the exact on-chain effect and ask for an acknowledgement in the human's own words before setting `irreversible_ack: true`.
- The three `signoff` flags work the same way: set each only after the human has said the thing in their own words, and quote them in the `.md` sign-off table with the date.
- When the chain, the spec and the human's memory disagree, show all three and ask.

## 6. What the agent must never do

- Broadcast to a live chain without the conditions: clean rehearsal (deploy and use), clean `plan_diff`, the three sign-offs, explicit "yes".
- Ask for, echo, log or commit a private key.
- Invent an address, a fuse, a market id, a feed or a price.
- Mark a check ✅ on data it did not fetch.
- Edit `contexts/` or `schema/` silently.
- Weaken a guard to make a run pass.
