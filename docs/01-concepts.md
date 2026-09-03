# 01 — Concepts: what you are building

This page gives the mental model needed for the rest of the manual. It is short on purpose; the authoritative protocol documentation is at [docs.ipor.io](https://docs.ipor.io). Append `.md` to any page URL there for plain Markdown, or start at `https://docs.ipor.io/llms.txt`, which indexes the whole site for language models.

## 1. IPOR Fusion in five concepts

**PlasmaVault.** An ERC-4626 vault with one immutable underlying asset (USDC, WETH, cbBTC, …). Depositors receive shares. The vault holds all assets itself; nothing is pooled across vaults. Vaults are not upgradeable, but they are configurable.

**Fuse.** A small, stateless, immutable adapter contract that the vault `delegatecall`s to act on one external protocol: supply to Aave, borrow on Morpho, swap through a router, claim rewards. Fuses come in families: *functional* (enter/exit), *balance* (report the vault's position value in a market), *reward*, *swap*. Every market the vault touches needs exactly one balance fuse. Fuses are shared across vaults; which fuses a vault may use is a per-vault configuration, and only fuses on the chain's on-chain `FuseWhitelist` can be added.

**Market and substrate.** Each protocol integration has a numeric market id (Aave V3 = 1, idle ERC20 balance = 7, Universal Token Swapper = 12, Morpho Blue = 14, …). *Substrates* are the per-market allow-list of what the fuse may touch: token addresses, Morpho market ids, Euler vaults, `(target, selector)` pairs for a swapper. If a substrate is not granted, the strategy cannot use it and the vault does not account for it.

**Roles.** Access control is an OpenZeppelin AccessManager extension with numeric roles. Operationally: `OWNER` (1) governs the role tree; `GUARDIAN` (2) can pause and cancel timelocked operations; `ATOMIST` (100) does day-to-day configuration; `ALPHA` (200) executes the strategy; `FUSE_MANAGER` (300), `PRE_HOOKS_MANAGER` (301), fee roles (901/902), oracle manager (1200), rewards roles (600/700/1100), balance-update role (1000), `WHITELIST` (800) for gated deposits. Roles can carry execution delays (timelocks). The role ids are in `deploy/roles.py`; the on-chain source is `Roles.sol` in [`IPOR-Labs/ipor-fusion`](https://github.com/IPOR-Labs/ipor-fusion).

**Atomist and Alpha.** The *Atomist* is the human who designs and governs the vault. The *Alpha* is whoever calls `execute([FuseAction…])` to move capital: a bot, a service, or a person with a wallet. A vault does nothing by itself; without an Alpha, deposits sit idle. This repository creates the vault; it does not run the Alpha.

Two configuration switches are **one-way**: a whitelist can be removed but never re-added, and share transferability can be enabled but never disabled. The DAO fee tier chosen at deployment is **immutable**.

## 2. The lifecycle this repository implements

```
 ┌──────────────┐   ┌───────────────┐   ┌───────────┐   ┌───────────────┐
 │ Human spec   │──►│ Machine spec  │──►│ Dry-run   │──►│ Fork          │
 │ (.md)        │   │ (.json)       │   │           │   │ rehearsal     │
 └──────────────┘   └───────────────┘   └───────────┘   └───────┬───────┘
                                                                │ human "yes"
                          ┌───────────────┐   ┌─────────────────▼──────┐
                          │ Verify        │◄──│ Live broadcast         │
                          │ on-chain      │   │ (needs the human's key)│
                          └───────────────┘   └────────────────────────┘
```

| Phase | Purpose | Needs a key? | Doc |
|---|---|---|---|
| Human spec | Capture intent and every decision, with provenance | no | `templates/strategy-spec.md` |
| Machine spec | Encode it as schema-validated JSON the deployer executes | no | `03-strategy-json.md` |
| Dry-run | Print the full plan, every call with decoded args, preview the vault's addresses | **no** | `04-deploy.md` |
| Fork rehearsal | Run the whole pipeline against a local anvil fork; verify on-chain; diff plan vs run | anvil's public test key | `04-deploy.md` |
| Live broadcast | Same pipeline, real chain, the human's funded key, the human's approval | **yes, the human's** | `04-deploy.md` |
| Verify | Read the live vault's own contracts against the spec | no | `04-deploy.md` |

A vault launches in a **trial posture**: whitelisted, non-transferable, one account holding every role, no timelocks. That is intentional. Moving to production (multisigs, timelocks, opening the whitelist) is a separate process outside this repository; see `07-resources.md`.

## 3. The artifacts

| Artifact | Produced by | Purpose | Committed? |
|---|---|---|---|
| `strategies/<name>.md` | intake | Human spec, the reviewer's source of truth | yes |
| `strategies/<name>.json` | agent or human | Machine spec; the only thing the deployer reads | yes |
| `contexts/<ctx>.json` | maintainers | Per-chain address book | yes |
| `.deploy-state/<name>.plan.json` | dry-run | Every intended on-chain action with decoded args | no |
| `.deploy-state/<name>.json` | `--broadcast` | Idempotency record: config hash, cloned addresses, completed steps, tx hashes | no |
| `.deploy-state/<name>.run.json` | `--broadcast` | Same shape as the plan plus tx hashes and gas | no |

`.deploy-state/` is git-ignored. If a human needs a durable record of a live deployment, copy the addresses into the `.md` spec and commit that.

## 4. Who does what

| Actor | In this repo | Typical holder |
|---|---|---|
| Human operator (Atomist) | Answers `[client]` questions, provides and funds the deployer key, approves the live broadcast, owns the outcome | the person you work with |
| Agent | Everything else: resolution, validation, JSON authoring, rehearsals, verification | you |
| Deployer key | Signs the pipeline; holds every role during the trial | an account the human controls, in `.env` |
| Alpha | Runs the strategy after deployment | a bot or service the human operates or commissions |
| IPOR Labs | Publishes the contracts, whitelists fuses on-chain, maintains `ipor-abi` and the docs | external party |

## 5. Trust order for facts

When two sources disagree, the higher one wins:

1. **The chain.** `FuseWhitelist.getFusesByMarketId`, the vault's `AccessManager`, the vault's own price manager, `getDependencyBalanceGraph`, `getTargetFunctionRole`.
2. **[`IPOR-Labs/ipor-abi`](https://github.com/IPOR-Labs/ipor-abi)** (`mainnet/<deployment>/addresses.json` and the README fuse lists).
3. **[`IPOR-Labs/ipor-fusion`](https://github.com/IPOR-Labs/ipor-fusion) source on `main`** for substrate layouts and fuse behaviour.
4. **`contexts/*.json`** in this repository, a manually maintained cache of (2).
5. **[docs.ipor.io](https://docs.ipor.io)** for concepts and procedures.
6. **Anything a person remembers.**
