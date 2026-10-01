"""Which roles the deployer needs, and which final grants the operator may skip.

Pure (no SDK), so the unit suite covers it. The function→role map comes from
`IporFusionAccessManagerInitializerLibV1` in the ipor-fusion repository:

  FUSE_MANAGER_ROLE                     addFuses / removeFuses, grantMarketSubstrates, addBalanceFuse,
                                        updateDependencyBalanceGraphs, updateCallbackHandler
  ATOMIST_ROLE                          setTotalSupplyCap, WithdrawManager.updateWithdrawWindow,
                                        FeeManager.updateManagementFee / updatePerformanceFee,
                                        convertToPublicVault, enableTransferShares; admin of WHITELIST,
                                        ALPHA, FUSE_MANAGER, CONFIG_INSTANT_WITHDRAWAL_FUSES, the
                                        withdraw-fee roles, PRICE_ORACLE_MIDDLEWARE_MANAGER, UPDATE_*
  PRICE_ORACLE_MIDDLEWARE_MANAGER_ROLE  PriceOracleMiddlewareManager.setAssetsPriceSources
  CONFIG_INSTANT_WITHDRAWAL_FUSES_ROLE  configureInstantWithdrawalFuses
  PRE_HOOKS_MANAGER_ROLE                setPreHookImplementations
  OWNER_ROLE (held from the clone)      admin of ATOMIST, GUARDIAN, PRE_HOOKS_MANAGER, OWNER

The base setup gives the deployer ATOMIST and FUSE_MANAGER, plus a role only when a
configured step calls a function restricted to it. Nothing else: the deployer never
receives ALPHA, GUARDIAN or the balance-updater roles just to configure a vault.
"""
from __future__ import annotations

# final grants that are never optional: who owns, configures and extends the vault
CORE_ROLES = ("OWNER_ROLE", "ATOMIST_ROLE", "FUSE_MANAGER_ROLE")

# held by the alpha (the strategy bot) and its keepers; granted later, once the alpha exists:
# `python -m deploy <strategy> --broadcast --assign-alpha` (make assign-alpha)
ALPHA_ROLES = ("ALPHA_ROLE", "UPDATE_MARKETS_BALANCES_ROLE", "UPDATE_REWARDS_BALANCE_ROLE",
               "CLAIM_REWARDS_ROLE", "TRANSFER_REWARDS_ROLE")


def bootstrap_roles(raw: dict) -> list[tuple[str, str]]:
    """(role, why) the deployer needs to run the configuration steps of this strategy."""
    out = [
        ("ATOMIST_ROLE", "supply cap, withdraw window, fees, whitelist and transferability; admin of most other roles"),
        ("FUSE_MANAGER_ROLE", "fuses, substrates, balance fuses, dependency graph, callback handlers"),
    ]
    if raw.get("price_feeds"):
        out.append(("PRICE_ORACLE_MIDDLEWARE_MANAGER_ROLE", "step 05: registers price feeds on the vault's own oracle"))
    if (raw.get("instant_withdrawal") or {}).get("order"):
        out.append(("CONFIG_INSTANT_WITHDRAWAL_FUSES_ROLE", "step 08: sets the instant-withdrawal order"))
    if raw.get("pre_hooks"):
        out.append(("PRE_HOOKS_MANAGER_ROLE", "step 09: installs pre-hooks"))
    return out


def grant_is_deferred(grant: dict) -> bool:
    """An alpha role: not granted by the base run, only by --assign-alpha."""
    return grant["role"] in ALPHA_ROLES


def grant_is_optional(grant: dict) -> bool:
    """A final grant the operator may skip on the signing page: anything outside the core
    roles, unless the strategy marks the grant `"required": true`."""
    return grant["role"] not in CORE_ROLES and not grant.get("required", False)
