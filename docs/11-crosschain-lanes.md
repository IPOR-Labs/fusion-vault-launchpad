# 11 — Cross-chain lanes (CCIP, Arbitrum ↔ HyperEVM)

A cross-chain lane lets a vault on one chain (the **hub**) hold positions in a vault on another chain (the **spoke**). Funds and commands travel over Chainlink CCIP. This page covers the first supported lane, USDC between Arbitrum and HyperEVM: which contracts are involved, what IPOR configures, what the vault operator sets up, and the gas rules that keep messages executing on their own.

Status (2026-10-23): the contracts are deployed on both chains and listed in the vendored ipor-abi snapshot. IPOR still has to configure the CCIP factory (creation codes, asset, routes); that takes at least 24 hours because of the factory timelock. `deploy.campaign` still draws `cross-chain` links without executing them; the steps below are manual until the SDK and this pipeline support them.

## Contracts

The same addresses on Arbitrum (42161) and HyperEVM (999). ipor-abi keys in brackets.

| Contract | Address |
|---|---|
| CCIP cross-chain factory (`CcipCrosschainFactory`) | `0xAd84580810A4e498F5F13Ad8506F73782FB472FA` |
| Supply fuse, market 54 (`CcipCrosschainSupplyFuse`) | `0x00a20FD711083CcE9C9c4292762aFf79f78C48eb` |
| Command fuse, market 54 (`CcipCrosschainCommandFuse`) | `0x997F12b31a6B1011ba844Bd0BC5fbeBCC7604793` |
| Claim fuse, market 54 (`CrosschainClaimFuse`) | `0x77bE16F7b33332BE2589E8E0582b397c524cF0d9` |
| Balance fuse, market 54 (`CrosschainBalanceFuse`) | `0x5896f376F06faB3964B1e060F8F3A86131D639c2` |
| NAV validation pre-hook (`CrosschainNavValidationPreHook`) | `0xA17c7A5Ed994aA5FE99C22B37DeCBCbfb32FaFe3` |

| Chain | CCIP chain selector | CCIP router (`CcipRouter` in the context) | USDC |
|---|---|---|---|
| Arbitrum | `4949039107694359620` | `0x141fa059441E0ca23ce184B6A78bafD2A517DdE8` | `0xaf88d065e77c8cC2239327C5EDb3A432268e5831` |
| HyperEVM | `2442541497099098535` | `0x13b3332b66389B1467CA6eBd6fa79775CCeF65ec` | `0xb88339CB7199b77E23DB6E890353E22632Ba630f` |

The market id is `CROSSCHAIN` = 54 (`IporFusionMarkets.sol`). `ipor-fusion` 3.7.0 does not know it, so both chain contexts list it under `markets`.

Moving parts:

- **Executor** (Arbitrum): created by the factory for one hub vault and one asset. It sends funds and commands to HyperEVM and holds the attested remote balance.
- **Dispatcher** (HyperEVM): deployed by the factory at the executor's address when the first "ticket" arrives. It deposits into and redeems from the spoke vaults it is allowed to use, and sends responses back.
- **Proposer and approver**: two separate keys that attest the remote balance on the executor. The hub's NAV depends on these attestations.

## What IPOR configures (factory, once per chain)

Done by the factory owner, not by vault operators. Listed so you know what must be in place before a lane can work:

1. Creation codes for the executor and dispatcher (one-time; HyperEVM needs a big block for this).
2. The USDC asset (`keccak256("USDC")`), the factory routes in both directions and the ticket gas, each behind a 24-hour timelock.

Check from any RPC: `creationCodesConfigured()` = true, `assetConfig(keccak256("USDC"))` returns the chain's USDC, `ccipRoute(<other chainId>)` is set and enabled.

## Vault-side setup (in this order)

The executor must exist before the hub's substrates are granted. HyperEVM must have the factory configuration, the spoke vault and the dispatcher budget before the ticket arrives.

1. **Hub vault** on Arbitrum: a USDC vault cloned from the FusionFactory as usual. The owner grants ATOMIST (100) and PRE\_HOOKS\_MANAGER (301); the atomist grants FUSE\_MANAGER (300), ALPHA (200) and WHITELIST (800) as needed.
2. **Spoke vault** on HyperEVM: a USDC vault with no markets. Keep it light (see "Gas rules"). Cloning needs approx. 9.3 M gas, so it goes into a big block (see "HyperEVM big blocks").
3. **Executor** address: pick a `SALT` and read `computeExecutorAddress(creator, SALT)` on the factory; it is the same on both chains. Then `createExecutor(SALT, keccak256("USDC"), HUB, safetyConfig)` on Arbitrum, from an allowed creator (`isAllowedCreator`). The creation freezes the asset. Agree the safety configuration (rescue admin, guardian, proposer, approver, delays) with IPOR first.
4. **Hub fuses**: `addFuses([supply, command, claim])` and `addBalanceFuse(54, balance fuse)`.
5. **Hub substrates on market 54**: `grantMarketSubstrates(54, [EXECUTOR, REMOTE_VAULT])`. Encoding: `type << 248 | chainId << 160 | address`, type 1 = executor (chainId 0), type 2 = remote vault (chainId 999, the spoke). This call replaces the whole list for the market, so always pass the full list.
6. **Hub pre-hook**: `setPreHookImplementations` with the NAV validation pre-hook on exactly these 7 selectors: `deposit` `0x6e553f65`, `depositWithPermit` `0x50921b23`, `mint` `0x94bf804d`, `withdraw` `0xb460af94`, `redeem` `0xba087652`, `redeemFromRequest` `0xa64099d2`, `execute` `0xbaae8abf`. Never on `updateMarketsBalances` or governance selectors: that would block the repair path. On a cloned vault this call needs role 301, whose admin is OWNER, not ATOMIST.
7. **Executor route**: the alpha executes `REGISTER_ROUTE` through the command fuse. The route must equal the factory route except `peer` = the executor.
8. **Budgets**: fund the executor on Arbitrum with ETH (it pays CCIP fees) and the dispatcher address on HyperEVM with HYPE (it pays for responses; approx. 0.0043 HYPE per response).
9. **Spoke access**: on the spoke's AccessManager grant WHITELIST\_ROLE (800) to the dispatcher address. Without it every DEPOSIT from the hub fails.
10. **Spoke USDC price**: register a USDC price source on the spoke's own price manager (`setAssetsPriceSources`, role 1200). The HyperEVM factory clones vaults without a USDC price; without it every REDEEM fails while DEPOSIT works, so the problem only shows at the first withdrawal.
11. **Dispatcher ticket**: the alpha executes `CREATE_DISPATCHER` with `[SPOKE]` through the command fuse. The ticket carries 7 M gas, so it never executes on its own on HyperEVM: execute it manually on the HyperEVM OffRamp from an account with big blocks, within 7 days. At most 3 attempts per executor and chain. The ACK back to Arbitrum arrives on its own.
12. **Canary**: a small supply, attestation, DEPOSIT, REDEEM, recall, zero attestation, claim. Every message to HyperEVM except the ticket should execute within minutes. Allow at least one hour: approvals are at least `minUpdateInterval` (1 h) apart.

**Closed-NAV window.** From the moment a transfer settles until the first approved attestation, the hub's `deposit`, `redeem` and `execute` revert (`ObservationStale`). Have the proposer and approver ready before the first supply.

## HyperEVM big blocks

HyperEVM has small blocks (3 M gas, about one per second) and big blocks (30 M gas, about one per minute). An account opts into big blocks through a HyperCore action (`evmUserModify` with `usingBigBlocks: true`); after that every transaction from that account goes into a big block. Use a separate account for big-block work, never the alpha key. Needed for: cloning the spoke vault, the factory's creation codes and the dispatcher ticket. Check an account with `eth_usingBigBlocks`.

## Gas rules

Messages to HyperEVM are executed automatically by the CCIP network only if they fit in a small block. The network's transaction limit is roughly the message gas + 365k.

- The Arbitrum→HyperEVM route uses **2,400,000** message gas. Do not raise it above approx. 2,500,000: every command would then need manual execution. The README of the contracts suggests 2.7 M; the code does not enforce it.
- Fuse data `gasLimit`: pass `0` (= the route policy). A higher value is capped to the policy; a lower one reverts.
- `UPDATE_VAULTS`: at most **22** vaults per message at 2.4 M (`CommandLaneGasTooLow` above that, nothing is lost). One removal per message.
- **Spoke weight**: a cold REDEEM or DEPOSIT on the spoke must stay under approx. 1.1 M gas and `convertToAssets` under approx. 1 M. A fresh vault without markets uses about 0.22 M (deposit) and 0.49 M (redeem). A heavier vault produces a FAILED + NACK (funds stay, the command can be cancelled). Measure on a fork before adding a vault.
- Commands expire after 24 hours (`COMMAND_TTL`).
- Before removing a vault from the dispatcher's list, withdraw everything from it.

Manual execution (the ticket, or any message stuck in state 0 or 3): take `encodedMessage` from the `CCIPMessageSent` log of the OnRamp, fetch the verifier result from the CCIP indexer, and call `execute(encodedMessage, ccvs, verifierResults, 0)` on the HyperEVM OffRamp with a transaction gas limit of the message's `executionGasLimit` + at least 200k. A gas limit that is too low only rejects the transaction; the message can be sent again.

## Pitfalls

1. The asset freezes at the first executor (Arbitrum) and the first dispatcher (HyperEVM).
2. Do not reuse a creator + `SALT` pair: an occupied dispatcher address means a permanent failure for that executor.
3. `feeToken` must be native, never USDC.
4. A spoke without WHITELIST\_ROLE for the dispatcher fails every DEPOSIT; a spoke without a USDC price fails every REDEEM.
5. `grantMarketSubstrates` replaces the market's list.
6. The pre-hook goes on the 7 selectors above only.
7. The ticket only manually, in a big block, within 7 days; at most 3 attempts.
8. Stargate is not usable on this route: Stargate v2 has no pool on HyperEVM and the default LayerZero DVN for the path is a dead DVN. Use CCIP.

## SDK support

`ipor-fusion` 3.7.0 (pinned here) and earlier encode the previous cross-chain message format (codec version 3, `Command` without `minExecutionGas`, supply fuse without `minDelivered`). Messages built with it will not decode on the deployed contracts. `ipor-fusion` 4.0.0 supports the deployed format (codec version 4, CCIP envelope 3) and its CCIP example targets the factory above; build cross-chain fuse data with 4.0.0 or later. Moving this repository to 4.0 is a breaking upgrade (`MIGRATION_4.md` in the SDK) and is not done yet.
