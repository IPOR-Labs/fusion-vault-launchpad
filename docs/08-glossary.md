# 08 — Glossary

| Term | Meaning |
|---|---|
| **AccessManager** | The OpenZeppelin-based access control contract (`IporFusionAccessManager`) that gates every restricted vault function by numeric role, with per-grant execution delays. |
| **Alpha** | Whoever calls `execute([FuseAction…])` on the vault: a bot, a service, or a person. Holds `ALPHA_ROLE` (200). |
| **Atomist** | The strategist who designs and governs the vault. Holds `ATOMIST_ROLE` (100). In this repo, "the human operator". |
| **Balance fuse** | The fuse that reports the vault's position value in one market. Exactly one per market. |
| **Chain context** | `contexts/<ctx>.json`: per-chain address book. A cache of `ipor-abi`. |
| **Contribution** | A fee that is burned, raising the remaining depositors' share price: request contribution and instant-withdraw contribution. Not revenue. |
| **DAO fee package** | The management/performance fee to the IPOR DAO, chosen by `dao_fee_package_index` at clone. Immutable. |
| **Dependency (balance) graph** | Per-market list of markets whose balances must be refreshed together. Every balance-fuse market except the idle balance depends on `ERC20_VAULT_BALANCE` (7). Derived by the deployer, verified on-chain. |
| **Deploy state** | `.deploy-state/<name>.json`: config hash, cloned addresses, completed steps, tx hashes. Makes the pipeline idempotent and resumable. |
| **Dry-run** | The pipeline without `--broadcast`: validates, previews the clone, prints every call, writes `.plan.json`, sends nothing, needs no key. |
| **Execution delay / timelock** | Seconds a role holder must wait between `schedule` and `execute` on the AccessManager. |
| **Fork rehearsal** | Running the full `--broadcast` pipeline against an anvil fork of the target chain. Mandatory before a live deployment. |
| **Fuse** | A stateless, immutable adapter contract the vault `delegatecall`s to act on one protocol. |
| **FuseWhitelist** | The on-chain registry of approved fuses per market id. The authority for "is this fuse available on this chain". |
| **FusionFactory** | The factory that clones a full vault stack: vault, access manager, fee, rewards, withdraw, context and price managers. |
| **FusionInstance** | The tuple of addresses returned by `clone`. |
| **Guardian** | `GUARDIAN_ROLE` (2). Defensive only: pause, and cancel scheduled operations. Should be independent of the Owner. |
| **Instant withdrawal queue** | Ordered `(fuse, params)` list the vault drains, after idle balance, to serve instant redemptions. Never includes collateralised markets. |
| **Live node / local node** | The pipeline classifies the RPC by `web3_clientVersion`: anvil or hardhat is a local fork; anything else is live and needs `--i-understand-this-is-live`. |
| **Market id** | Numeric id of a protocol integration (`IporFusionMarkets.sol`): Aave V3 = 1, ERC20 idle = 7, Universal swapper = 12, Morpho = 14, … |
| **One-way switch** | Whitelist removal and share transferability. Irreversible. |
| **Owner** | `OWNER_ROLE` (1). Roots the role tree. |
| **Plan artifact** | `.deploy-state/<name>.plan.json` (dry-run) / `.run.json` (broadcast): manifest plus ordered actions. Diffed by `tools/plan_diff.py`. |
| **PlasmaVault** | The Fusion vault contract: ERC-4626, one underlying, delegates to fuses and managers. |
| **Pre-hook** | A contract run before a restricted vault function: exchange-rate validator, pause, update-balances. |
| **Price manager** | The vault's own `PriceOracleMiddlewareManager`, falling back to the chain's shared `PriceOracleMiddleware`. Feeds are registered on the vault's own manager. |
| **Redemption delay** | Minimum seconds between a deposit and a withdraw/redeem/transfer of those shares. Immutable after clone. |
| **Share decimals** | Underlying decimals plus the vault's offset. Caps are expressed in share units on-chain; the pipeline converts from underlying for you. |
| **Standard fuses** | Fuses the factory injects into every vault (`BurnRequestFeeFuse`). Not declared in the JSON; upgraded by `02b_standard_fuses`. |
| **Substrate** | A per-market allow-list entry (bytes32): token address, Morpho market id, Euler vault, `(target, selector)`, Aave V4 reserve. Encoding depends on the fuse family. |
| **Trial posture** | Whitelisted, non-transferable, one account holding every role, no timelocks. The state a vault launches in. |
| **Whitelist** | `WHITELIST_ROLE` (800) gating deposit/mint. Removable once, never re-enabled. |
| **Withdraw window** | For scheduled withdrawals: how long a request stays valid. Not set for instant-only vaults. |
