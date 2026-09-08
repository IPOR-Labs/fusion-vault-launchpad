# Contributing

Thank you. This repository moves real funds, so contributions are held to the same standard as the pipeline itself: deterministic, tested, traceable to a public source.

## Ground rules

- **English** for code, comments, docs, commit messages and specs.
- **Every on-chain fact needs a public source.** Fuse addresses, selectors, substrate layouts and role ids must be traceable to [`IPOR-Labs/ipor-abi`](https://github.com/IPOR-Labs/ipor-abi), [`IPOR-Labs/ipor-fusion`](https://github.com/IPOR-Labs/ipor-fusion) on `main`, or a live read. Record the date in the commit.
- **Pure core, thin I/O.** Decidable logic (encoders, graph derivation, guards, plan diff) lives in SDK-free modules under `deploy/` and is unit-tested. Chain I/O lives at the edges (`sdk_session.py`, the step modules, `verification.py`).
- **Never weaken a guard to make a run pass.** If a check blocks you, the config is wrong or the check is wrong; fix the right one and add a test.
- **Incidents become checks.** When a deploy goes wrong, the fix is a verification row, a loader rule or a guard, plus a line in `AGENTS.md` §8 and `docs/06-troubleshooting.md`.
- **No private dependencies.** Nothing in this repository may require access to IPOR Labs internal systems, private repositories, internal MCP servers or private boards. If a public equivalent does not exist, the feature does not belong here.

## Development setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest              # SDK-free; must stay green in CI (Python 3.13)
```

CI installs only the lightweight test deps and runs `pytest`, then validates every file under `strategies/` and `contexts/` against the schemas.

## Common contributions

### Adding a chain

1. Create `contexts/<ctx>.json` following `schema/deploy-context.schema.json`. Copy proxy addresses from `ipor-abi/mainnet/<deployment>/addresses.json`; never `…Impl` addresses. Set `public_rpc` to a read-only public endpoint.
2. Fill `standard_fuses` oldest → newest (the factory-injected fuse and the whitelisted latest).
3. Run a dry-run and a fork rehearsal for at least one strategy on the chain before opening the PR, and attach the verification report.

### Adding a fuse to a context

1. Confirm it is whitelisted: `getFusesByMarketId(marketId)` on the chain's `FuseWhitelist`.
2. Confirm `MARKET_ID()` on the fuse.
3. Name it consistently with the `ipor-abi` README.
4. If it is a new **family** with its own substrate layout, add an encoder (and a decoder for canonical checks) in `deploy/encoders/substrates.py`, register it, extend the schema enum, and add golden tests in `tests/test_encoders.py`. Add the name → encoding rule in `deploy/swapper.py` if it is a swapper.
5. If it can sit on the instant queue, add its minimum params to `QUEUE_MIN_PARAMS` in `deploy/fuses.py`.

### Adding a deploy step

Steps are modules in `deploy/steps/` exposing `NAME` and `run(cfg, deploy_ctx, session, instance, state, broadcast)`. Register the module in `_STEP_MODULES` in `deploy/steps/__init__.py` (order matters; document the new index in `docs/04-deploy.md`). A step must:

- skip itself when `state.has_step(NAME)`;
- record every intended action through `session.recorder.add(...)` **before** sending, and fill `executed` / `tx_hash` / `gas_used` after;
- call `state.record(NAME, tx_hashes, notes)` only on broadcast;
- never persist a dry-run result that a later broadcast could mistake for real state.

Add a verification row in `deploy/verification.py` for whatever the step configures.

### Adding a rehearsal script

A strategy that declares functional fuses needs `rehearsals/<name>.py` exposing `build_batches(env, stage) -> list[RehearsalBatch]` (`deploy/rehearsal_rules.py`). Build the actions with the SDK fuse wrappers, copying the closest walk in the SDK's `tests/test_simulate_*.py`; declare every fuse a batch exercises. `stage == "open"` runs after the deposit, `"unwind"` before the withdrawal. Keep the numbers derived from `env` reads (balances, positions), never hard-coded, so the script survives a different fork block.

## Pull requests

- One concern per PR.
- Tests for every pure change; a fork rehearsal log (or `--verify-only` output) for every step or encoder change.
- Update the docs page that describes what you touched.
- Do not commit anything under `.deploy-state/`, `.env`, or a real client's strategy files without their consent.

## Reporting a problem

Open an issue with: the command, the strategy JSON (redact addresses if needed), the chain and RPC type (public, private, fork), the full output, and the `.plan.json` / `.run.json` if a deploy was involved. For anything that looks like a security issue in the Fusion contracts themselves, use the disclosure channels listed at [docs.ipor.io](https://docs.ipor.io) rather than a public issue.
