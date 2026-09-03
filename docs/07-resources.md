# 07 — Public resources

Everything this repository depends on, and where to look things up. Nothing here requires an account with IPOR Labs.

## IPOR Fusion

| Resource | Use it for |
|---|---|
| [docs.ipor.io](https://docs.ipor.io) | Protocol documentation: PlasmaVault, fuses, roles, oracles, withdrawals, fees, audits. Append `.md` to any page URL for Markdown; `https://docs.ipor.io/llms.txt` indexes the site for language models. |
| [`IPOR-Labs/ipor-fusion`](https://github.com/IPOR-Labs/ipor-fusion) | Solidity source of the vault, managers, fuses and `Roles.sol`. Read fuse source on `main` for substrate layouts and enter/exit params. |
| [`IPOR-Labs/ipor-abi`](https://github.com/IPOR-Labs/ipor-abi) | Deployed addresses per chain (`mainnet/<deployment>/addresses.json`), ABIs, and the README fuse lists. The source for `contexts/*.json`. |
| [`IPOR-Labs/ipor-fusion.py`](https://github.com/IPOR-Labs/ipor-fusion.py) / [PyPI `ipor-fusion`](https://pypi.org/project/ipor-fusion/) | The Python SDK this pipeline uses (`FusionFactory`, `Web3Context`, market ids). Its CLI also offers read-only vault inspection, for example `ipor-fusion vault health <address>`. |
| [`IPOR-Labs/ipor-fusion-alpha-example`](https://github.com/IPOR-Labs/ipor-fusion-alpha-example) | Reference implementation of an Alpha (the bot that executes fuse actions). Out of scope here, but the natural next step after deployment. |
| [app.ipor.io](https://app.ipor.io) | The web application; useful to inspect a deployed vault and, for role holders, to perform configuration actions by hand. |

## On-chain reads you will do often

| Question | Where to read |
|---|---|
| Which fuses are available for market X on this chain? | `FuseWhitelist.getFusesByMarketId(marketId)` at the context's `fuse_whitelist` |
| What market does this fuse serve? | `MARKET_ID()` on the fuse |
| What are the DAO fee packages? | `getDaoFeePackage(uint256)` on the `FusionFactory` |
| Can the vault price asset A? | `getAssetPrice(A)` on the vault's own price manager |
| Who holds role R? | the vault's `AccessManager` (events `RoleGranted`, or `hasRole`) |
| Is the whitelist / transferability switch on? | `getTargetFunctionRole` for `deposit` / `mint` and `transfer` / `transferFrom` on the vault |

`cast call` from Foundry, or web3 in Python, is enough for all of these.

## Tooling

| Resource | Use it for |
|---|---|
| [Foundry](https://getfoundry.sh) (`anvil`, `cast`) | Fork rehearsals and ad-hoc reads. [Anvil docs](https://book.getfoundry.sh/anvil/). |
| RPC providers | Any provider works. Use a private endpoint for broadcasts. Public endpoints used for dry-runs are listed per chain in `contexts/*.json`. |
| Block explorers | Etherscan, Basescan and others, to confirm receipts and read verified contract source. |

## External protocols

When a strategy touches a protocol, the protocol's own documentation and address lists are the source of truth for market ids, oracles, pool and vault addresses. Common ones: Aave, Morpho, Euler, Spark, Fluid, Odos, Chainlink feeds. Always confirm a feed's `updatedAt` on-chain before using it.

## What is out of scope here, and where to go

| Need | Direction |
|---|---|
| Running the strategy (the Alpha) | `ipor-fusion-alpha-example`, or an engagement with a keeper operator |
| Hardening for production (multisigs, timelocks, opening the whitelist, enabling transfers) | docs.ipor.io role and access pages; `app.ipor.io` for role holders; a future module of this repository |
| Listing a vault publicly on app.ipor.io | contact IPOR Labs through the channels on docs.ipor.io |
