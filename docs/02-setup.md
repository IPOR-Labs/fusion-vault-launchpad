# 02 — Setup

## 1. Prerequisites

| Requirement | Why | Notes |
|---|---|---|
| Python **3.12+** (CI runs 3.13; 3.14 works) | all tooling | |
| `pip install -r requirements-dev.txt` | the [IPOR Fusion Python SDK](https://pypi.org/project/ipor-fusion/), web3, jsonschema, pytest | pinned versions |
| [Foundry](https://getfoundry.sh) (`anvil`, `cast`) | fork rehearsals and ad-hoc reads | optional for dry-runs, required before any live deployment |
| An RPC endpoint per target chain | everything on-chain | a **private** endpoint (Alchemy, Infura, QuickNode, your own node) for any broadcast; the public endpoints in `contexts/` are fine for dry-run reads |
| A deployer account with gas | live deployment only | never paste the key into shell history or a chat; use `.env` |

## 2. Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest            # expect: all passed, a few seconds, no network
```

Run every command from the repository root.

## 3. Environment variables

Copy `.env.example` to `.env`. The file is git-ignored and loaded automatically by the pipeline.

| Variable | Meaning | Needed for |
|---|---|---|
| `RPC_URL` | The endpoint every read and write goes to. Always wins when set. | **Required** for any `--broadcast`. Optional for a dry-run: if unset, the pipeline uses the read-only `public_rpc` from `contexts/<chain>.json`. |
| `DEPLOYER_PRIVATE_KEY` | Hex with `0x` prefix. The account that signs the clone and every configuration transaction, and holds every role during the trial. | **Required only for `--broadcast`.** A dry-run without it prints the whole plan with a zero signer. |

There is no other configuration. The pipeline never phones home, never reads any other secret, and never uses the key for anything but signing the transactions it prints.

### Which key goes where

| Target | `RPC_URL` | `DEPLOYER_PRIVATE_KEY` |
|---|---|---|
| Dry-run | unset, or any endpoint of the target chain | unset |
| Fork rehearsal | `http://127.0.0.1:8546` (your anvil) | anvil default account #0: `0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80` (public key material, never holds real funds) |
| Live deployment | your private endpoint for the target chain | the operator's own funded key |

The pipeline detects whether the node it talks to is a local fork (anvil, hardhat) or a live chain. A live chain additionally requires the `--i-understand-this-is-live` flag and refuses any configuration that still contains anvil's default accounts.

## 4. Verify the installation

```bash
python -m pytest
python -m deploy strategies/example-usdc-aave-base.json      # dry-run; no key, no RPC_URL needed
```

The dry-run prints the 16-step plan with decoded calldata, previews the deterministic addresses of the vault and its managers, writes `.deploy-state/example-usdc-aave-base.plan.json`, and ends with `DRY-RUN COMPLETE`. If it does, the SDK imports, the public RPC is reachable and the config loads.

## 5. Fork setup with anvil

Always start a **fresh** anvil for each rehearsal. A leftover fork, often of a different chain, silently corrupts results.

```bash
# free the port
lsof -ti :8546 | xargs -r kill

# fork the target chain (Base shown; use an Ethereum RPC for Ethereum strategies)
anvil --fork-url https://mainnet.base.org --port 8546
```

The fork source must serve **archive reads** (state at the pinned fork block). Some public endpoints refuse those without an API key, and the first transaction on the fork then fails with `Fork Error … 403`. Base's official endpoint `https://mainnet.base.org` worked in our runs; a private RPC (Alchemy, Infura, QuickNode) is the reliable choice. `make fork` wraps this. Verify the fork is the chain you think it is:

```bash
cast chain-id --rpc-url http://127.0.0.1:8546            # 8453 for Base, 1 for Ethereum
cast code <fusion_factory from the context> --rpc-url http://127.0.0.1:8546 | head -c 10   # must not be 0x
```

Anvil mirrors the forked chain's id, so the strategy's `execution.fork_chain_id_allowlist` needs the target chain id (already there) and `31337` only if you run anvil without `--fork-url`.

Anvil funds its default accounts with 10,000 ETH. If you use a different deployer on the fork:

```bash
cast rpc anvil_setBalance <deployer> 0x21E19E0C9BAB2400000 --rpc-url http://127.0.0.1:8546
```

## 6. Directory hygiene

- `.deploy-state/` holds run state and artifacts. It is git-ignored. Do not delete a state file for a strategy that has a **live** deployment; it is the only local record of what was broadcast.
- After a fork rehearsal, the state file holds fork addresses. Delete it (or pass `--force-restart`) before the live run, otherwise the pipeline believes the vault already exists.
