# Fusion Vault Launchpad

**Deploy an [IPOR Fusion](https://docs.ipor.io) PlasmaVault from a JSON spec: simulate for free, rehearse on a fork, then create it for real.**

> **Status: work in progress.** Shared as-is under the MIT license. It can broadcast real transactions, so review generated plans, rehearse on a fork first, and treat every output as input to your own judgement. See [`DISCLAIMER.md`](./DISCLAIMER.md).

This repository is self-contained and public. It depends only on public resources: the [IPOR Fusion Python SDK](https://github.com/IPOR-Labs/ipor-fusion.py) on PyPI, the contracts and addresses published by IPOR Labs on GitHub, [docs.ipor.io](https://docs.ipor.io), an RPC endpoint of your choice, and optionally [Foundry](https://getfoundry.sh) for fork rehearsals.

It is written to be driven by an **AI coding agent** working together with a **human operator**, and it does not depend on any particular AI product: the instructions live in [`AGENTS.md`](./AGENTS.md), and pointer files exist for every common tool (see [`docs/09-using-with-your-agent.md`](./docs/09-using-with-your-agent.md)). Agents: start at `AGENTS.md`. Humans: keep reading.

## What it does

You describe a vault in one file, `strategies/<name>.json`: chain, underlying asset, the protocols it may use (fuses), what it may touch in them (substrates), price feeds, fees, withdrawal mode, roles, whitelist and transferability. The deployer turns that file into a configured vault in 17 idempotent steps and verifies the result by reading the vault's own contracts.

Three modes, one command:

| Mode | Command | Needs | Sends anything? |
|---|---|---|---|
| **Dry-run (simulation)** | `python -m deploy strategies/<name>.json` | Python, internet | No. Prints every intended call with decoded arguments and writes a plan file. |
| **Fork rehearsal** | same, with `--broadcast --rehearse` and `RPC_URL` pointing at a local [anvil](https://book.getfoundry.sh/anvil/) fork | Foundry, an archive-capable RPC to fork from, anvil's built-in test key | Only to your local fork. Full pipeline, on-chain verification, then the vault is **used**: deposit, every fuse executed, accounting checked, withdrawal paid. |
| **Live deployment** | same, with `--broadcast --i-understand-this-is-live` | your private RPC, **your funded deployer private key** in `.env`, a human "yes" | Yes. Creates a real vault. |

Simulation never needs a private key. Creating a vault always does. The pipeline refuses a live broadcast when any role, whitelist entry or fee recipient is a well-known test account.

## Quick start

```bash
git clone <this repository> && cd fusion-vault-launchpad
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python tools/doctor.py                                    # what works in this environment (never prints secrets)
python -m pytest                                          # unit tests, no chain needed
python -m deploy strategies/example-usdc-aave-base.json   # dry-run of the shipped example (no key, no RPC config)
```

`make help` lists the same commands as make targets (`make doctor`, `make test`, `make dry-run`, `make fork`, `make rehearse`, `make diff`, `make verify`).

Two examples ship, both on Base and both full of anvil placeholders the pipeline will not let reach a live chain: `example-usdc-aave-base`, a single-venue USDC vault supplying to Aave V3, and `example-wsteth-usdc-loop-base`, a leveraged wstETH/USDC loop on Morpho Blue that exercises a collateralised market, a flash-loan fuse with its callback handler, the legacy universal swapper and scheduled withdrawals.

To deploy your own vault:

1. Copy `strategies/example-usdc-aave-base.json` and `.md` to `strategies/<your-name>.*` and fill them in ([`docs/03-strategy-json.md`](./docs/03-strategy-json.md)).
2. Dry-run until the plan reads exactly as intended.
3. Rehearse on an anvil fork of the target chain with `--broadcast --rehearse` and check the verification report, the rehearsal report and the plan/run diff ([`docs/04-deploy.md`](./docs/04-deploy.md)).
4. Collect the three sign-offs from the human ([`docs/05-human-in-the-loop.md`](./docs/05-human-in-the-loop.md) §4) and read [`docs/10-hardening.md`](./docs/10-hardening.md) together.
5. Put your deployer key and private RPC in `.env` (copy `.env.example`), fund the key, and broadcast.
6. Run `--verify-only` any time afterwards; harden before production ([`docs/10-hardening.md`](./docs/10-hardening.md)).

## Documentation

| Read this | To |
|---|---|
| [`AGENTS.md`](./AGENTS.md) | operate this repo as an AI agent: rules, autonomy boundary, what to ask the human |
| [`docs/01-concepts.md`](./docs/01-concepts.md) | understand vaults, fuses, substrates, roles and the two one-way switches |
| [`docs/02-setup.md`](./docs/02-setup.md) | install, configure `.env`, start a fork |
| [`docs/03-strategy-json.md`](./docs/03-strategy-json.md) | write the strategy file, field by field |
| [`docs/04-deploy.md`](./docs/04-deploy.md) | dry-run, fork rehearsal, live broadcast, verification, recovery |
| [`docs/05-human-in-the-loop.md`](./docs/05-human-in-the-loop.md) | what only the human decides, and how the key is handled |
| [`docs/06-troubleshooting.md`](./docs/06-troubleshooting.md) | symptoms, causes, fixes |
| [`docs/07-resources.md`](./docs/07-resources.md) | every public resource this repo relies on |
| [`docs/08-glossary.md`](./docs/08-glossary.md) | terms |
| [`docs/10-hardening.md`](./docs/10-hardening.md) | harden a rehearsed vault for production, and what the human must know before going live |
| [`docs/09-using-with-your-agent.md`](./docs/09-using-with-your-agent.md) | how Codex, Claude Code, Gemini, Cursor, Copilot, Windsurf, Grok, Aider and others pick up the instructions; a starter prompt |

## Verified so far

"Verified" here has a fixed meaning: the vault can be deployed; its fuses and substrates were **executed on a fork**, not only read back; it has the price feeds it needs; its accounting shows no double counting; a simulated deposit and withdrawal succeeded; and the human knows which addresses hold which roles, that the vault must be hardened before production (this repository helps), and that listing on the IPOR front end requires the IPOR Labs team. The last three are the `signoff` flags the pipeline demands before a live broadcast.

| Path | Status |
|---|---|
| Fresh install from PyPI, unit tests, schema checks, spec lint of both examples | CI on every commit |
| Dry-run of both examples without key or RPC config | run by a fresh agent from the docs alone |
| Aave example: full pipeline on a Base fork, verification 10/10, rehearsal 6/6 (deposit, Aave supply, instant withdrawal through the queue) | run on 2026-09-08 with `ipor-fusion` 3.6.3 |
| Loop example: full pipeline on a Base fork incl. the callback handler, verification 10/10, rehearsal 7/7 (deposit, 2.5x flash-loan loop at −38 bps, unwind, scheduled withdrawal via request → release → redeem) | run on 2026-09-08 with `ipor-fusion` 3.6.3 |
| Live deployment | by you, with your key, after your own rehearsal and the three sign-offs |

## Repository map

```
deploy/            the pipeline: cli.py (entry), config, context, sdk_session, guards, steps/, encoders/, verification
schema/            JSON schemas for strategies and chain contexts
contexts/          per-chain address books (FusionFactory, fuses, feed factories, pre-hooks); a cache of public on-chain data
strategies/        your strategy specs (.json machine spec + .md human spec); two shipped examples
rehearsals/        per-strategy rehearsal scripts: the SDK fuse actions the fork rehearsal executes
templates/         the human spec template
tools/             doctor.py (environment check), spec_lint.py (md ↔ json), plan_diff.py (plan ↔ run)
Makefile           the same commands as make targets; `make help`
tests/             SDK-free unit tests
docs/              the manual
.deploy-state/     run state and artifacts (git-ignored, created on first run)
```

## Scope

This repository covers **creating, configuring, verifying and rehearsing** a vault, and it helps the agent **harden** it for production ([`docs/10-hardening.md`](./docs/10-hardening.md): role handover to multisigs, execution delays, fee recipients, market limits; the deployer's renounce and the one-way switches stay manual). Operating the strategy day to day (the "Alpha" that moves funds) and monitoring are outside its scope; pointers are in [`docs/07-resources.md`](./docs/07-resources.md).

Pointer files for agent tools (`CLAUDE.md`, `GEMINI.md`, `CONVENTIONS.md`, `.cursor/`, `.windsurf/`, `.grok/`, `.github/copilot-instructions.md`) all say one thing: read `AGENTS.md`.

## Contributing and license

See [`CONTRIBUTING.md`](./CONTRIBUTING.md). MIT, see [`LICENSE`](./LICENSE). Not financial advice; see [`DISCLAIMER.md`](./DISCLAIMER.md).
