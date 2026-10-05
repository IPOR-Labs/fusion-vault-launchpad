"""Per-chain deploy context: the ipor-abi snapshot plus optional local overrides.

A strategy names its chain context (`chain.context`, e.g. `base-fusion`). That name selects
the vendored ipor-abi snapshot `contexts/ipor-abi/mainnet-<name>.json` (`deploy/ipor_abi.py`),
and every IPOR contract is looked up there by its ipor-abi key:

  factory, oracle middleware,     fixed keys, the same on every chain (`IporFusionFactoryProxy`,
  FuseWhitelist, standard fuses   `PriceOracleMiddlewareUsdWithRolesProxy`, `BurnRequestFeeFuse`...)
  price-feed factories            `<name>Proxy`, e.g. ERC4626PriceFeedFactory -> ERC4626PriceFeedFactoryProxy
  pre-hooks, callback handlers    the name itself (three legacy pre-hook names are aliased below)
  fuses                           the FuseWhitelist decides (`deploy/fuse_resolver.py`); ipor-abi
                                  under the same name is the fallback and the cross-check

`contexts/<name>.json` is optional and holds only what cannot be derived: a public RPC for
dry-runs, pins (an address chosen over the registries, with the reason), third-party addresses
with their source, token symbols, market ids the SDK does not know, and per-chain key overrides.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eth_typing import ChecksumAddress
from web3 import Web3

from deploy.ipor_abi import Snapshot, is_address, load_snapshot

CONTEXTS_DIR = Path(__file__).resolve().parent.parent / "contexts"

# Root contracts by ipor-abi key; identical on every chain. A context may override one under `contracts`.
DEFAULT_CONTRACTS = {
    "fusion_factory": "IporFusionFactoryProxy",
    "price_oracle_middleware": "PriceOracleMiddlewareUsdWithRolesProxy",
    "fuse_whitelist": "IporFusionFuseWhitelistProxy",
    "middleware_owner": "IporFusionPriceOracleMiddlewareWithRolesOwner",   # fork impersonation only
    "morpho_blue": "Morpho",
}
REQUIRED_CONTRACTS = ("fusion_factory", "price_oracle_middleware", "fuse_whitelist")
# Factory-injected fuses, oldest first; those absent from a chain's snapshot are skipped.
DEFAULT_STANDARD_FUSES = ("BurnRequestFeeFuse", "BurnRequestFeeFuseV2")
# Pre-hook names strategies have used that ipor-abi spells differently.
PRE_HOOK_ALIASES = {
    "PauseFunctionPreHook": "PreHookPauseFunction",
    "UpdateBalancesPreHook": "PreHookUpdateBalances",
    "UpdateBalancesIgnoreDustPreHook": "PreHookUpdateBalancesIgnoreDust",
}
# Chain ids by context name, for tools that run without a strategy; a context file may set chain_id.
CHAIN_IDS = {"mainnet-ethereum-fusion": 1, "base-fusion": 8453, "arbitrum-fusion": 42161, "hyperevm-fusion": 999}


def deployment_for(name: str) -> str:
    """ipor-abi deployment folder for a context name: `base-fusion` -> `mainnet-base-fusion`."""
    return name if name.startswith("mainnet-") else f"mainnet-{name}"


@dataclass(slots=True)
class DeployContext:
    raw: dict[str, Any]          # the optional overrides file ({} when absent)
    name: str
    snapshot: Snapshot
    chain_id_hint: int | None = None
    _fuse_overrides: dict[str, str] = field(default_factory=dict)

    # --- registry lookups ---------------------------------------------------------

    def contract_key(self, ref: str) -> str | None:
        return self.raw.get("contracts", {}).get(ref) or DEFAULT_CONTRACTS.get(ref)

    def contract_refs(self) -> list[str]:
        """Root contract names this chain resolves (defaults present in the snapshot, plus overrides)."""
        refs = [r for r in DEFAULT_CONTRACTS if self.snapshot.get(DEFAULT_CONTRACTS[r]) or r in REQUIRED_CONTRACTS]
        return refs + [r for r in self.raw.get("contracts", {}) if r not in refs]

    def _contract(self, ref: str) -> ChecksumAddress:
        key = self.contract_key(ref)
        if not key:
            raise KeyError(f"no ipor-abi key for contract '{ref}' (add it under contracts in contexts/{self.name}.json)")
        return self.snapshot.require(key, f"{self.name} contract {ref}")

    def pin(self, name: str) -> ChecksumAddress | None:
        p = self.raw.get("pins", {}).get(name)
        return Web3.to_checksum_address(p["address"]) if p else None

    def pins(self) -> dict[str, ChecksumAddress]:
        return {n: Web3.to_checksum_address(p["address"]) for n, p in self.raw.get("pins", {}).items()}

    def registry_fuse(self, name: str) -> ChecksumAddress | None:
        """What ipor-abi lists under this fuse name, None when it has no such key."""
        return self.snapshot.get(self.raw.get("aliases", {}).get(name, name))

    def pre_hook_key(self, name: str) -> str:
        return self.raw.get("aliases", {}).get(name) or PRE_HOOK_ALIASES.get(name, name)

    def feed_factory_key(self, name: str) -> str:
        alias = self.raw.get("aliases", {}).get(name)
        if alias:
            return alias
        return f"{name}Proxy" if self.snapshot.get(f"{name}Proxy") else name

    # --- public API used by the steps ---------------------------------------------

    @property
    def chain_id(self) -> int:
        cid = self.raw.get("chain_id") or self.chain_id_hint or CHAIN_IDS.get(self.name)
        if cid is None:
            raise KeyError(f"chain id unknown for context '{self.name}': set chain_id in contexts/{self.name}.json")
        return int(cid)

    @property
    def fusion_factory(self) -> ChecksumAddress:
        return self._contract("fusion_factory")

    @property
    def price_oracle_middleware(self) -> ChecksumAddress:
        return self._contract("price_oracle_middleware")

    @property
    def public_rpc(self) -> str | None:
        """Read-only public endpoint for dry-runs when RPC_URL is unset. Never used for --broadcast."""
        return self.raw.get("public_rpc") or None

    @property
    def middleware_owner(self) -> ChecksumAddress | None:
        """Holder of the manager role on the shared PriceOracleMiddleware (fork impersonation only)."""
        key = self.contract_key("middleware_owner")
        return self.snapshot.get(key) if key else None

    @property
    def fuse_whitelist(self) -> ChecksumAddress:
        """On-chain FuseWhitelist: the authority for which fuse address is current."""
        return self._contract("fuse_whitelist")

    def fuse(self, name: str) -> ChecksumAddress:
        """Address for a fuse name: the FuseWhitelist resolution (installed as an override by
        `deploy/fuse_resolver.py`), else a pin, else ipor-abi under the same name."""
        addr = self._fuse_overrides.get(name) or self.pin(name) or self.registry_fuse(name)
        if not addr:
            raise KeyError(f"fuse '{name}' not resolved on the FuseWhitelist, not pinned and not an ipor-abi key "
                           f"for context '{self.name}'")
        return Web3.to_checksum_address(addr)

    def set_fuse_override(self, name: str, address: str) -> None:
        self._fuse_overrides[name] = Web3.to_checksum_address(address)

    def standard_fuse_keys(self) -> list[str]:
        if "standard_fuses" in self.raw:
            return list(self.raw["standard_fuses"])
        return [k for k in DEFAULT_STANDARD_FUSES if self.snapshot.get(k)]

    def standard_fuses(self) -> list[ChecksumAddress]:
        """Fuses the FusionFactory injects into every vault (e.g. BurnRequestFeeFuse), oldest first.

        Always present on-chain but never declared in a strategy JSON, so the
        verifier treats them as expected rather than 'extra' (the request-fee
        burn is part of the standard withdraw/redemption flow)."""
        return [self.snapshot.require(k, f"{self.name} standard fuse") for k in self.standard_fuse_keys()]

    def pre_hook(self, name: str) -> ChecksumAddress:
        addr = self.pin(name) or self.snapshot.get(self.pre_hook_key(name))
        if not addr:
            raise KeyError(f"pre-hook '{name}' is not an ipor-abi key in {self.snapshot.deployment} "
                           f"(looked up '{self.pre_hook_key(name)}')")
        return addr

    def callback_handler(self, name: str) -> ChecksumAddress:
        """Stateless callback-handler contract (e.g. CallbackHandlerMorpho) by its ipor-abi key."""
        return self.snapshot.require(self.raw.get("aliases", {}).get(name, name), f"{self.name} callback handler {name}")

    def external(self, name: str) -> ChecksumAddress | None:
        e = self.raw.get("external_addresses", {}).get(name)
        return Web3.to_checksum_address(e["address"]) if e else None

    def token(self, symbol: str) -> ChecksumAddress:
        tokens = self.raw.get("tokens", {})
        if symbol not in tokens:
            raise KeyError(f"token '{symbol}' not listed in contexts/{self.name}.json; give the address instead")
        return Web3.to_checksum_address(tokens[symbol])

    def resolve_address(self, ref: str) -> ChecksumAddress:
        """An address literal, or a name: a root contract (`morpho_blue`), an external address
        (`aave_v3_pool`), a token symbol, or any ipor-abi key."""
        if is_address(ref):
            return Web3.to_checksum_address(ref)
        if ref in DEFAULT_CONTRACTS or ref in self.raw.get("contracts", {}):
            return self._contract(ref)
        if ref in self.raw.get("external_addresses", {}):
            return self.external(ref)
        if ref in self.raw.get("tokens", {}):
            return self.token(ref)
        if self.snapshot.get(ref):
            return self.snapshot.get(ref)
        raise KeyError(f"'{ref}' is neither an address nor a contract, external address, token or ipor-abi key "
                       f"for context '{self.name}'")

    def price_feed_factory(self, name: str) -> ChecksumAddress:
        return self.snapshot.require(self.feed_factory_key(name), f"{self.name} price-feed factory {name}")

    def known_addresses(self) -> set[str]:
        """Every address this context can resolve to (lower-case), for cross-checks such as spec-lint."""
        out = {a.lower() for a in self.snapshot.addresses.values() if is_address(a) and int(a, 16) != 0}
        out |= {a.lower() for a in self.pins().values()}
        out |= {e["address"].lower() for e in self.raw.get("external_addresses", {}).values()}
        out |= {a.lower() for a in self.raw.get("tokens", {}).values() if is_address(a)}
        return out

    def market_id(self, name: str) -> int:
        # Falls back to ipor_fusion.market_ids if not in context.
        markets = self.raw.get("markets", {})
        if name in markets:
            return int(markets[name])
        from ipor_fusion.market_ids import IporFusionMarkets
        if hasattr(IporFusionMarkets, name):
            return int(getattr(IporFusionMarkets, name))
        raise KeyError(f"market '{name}' not defined")


def load_context(name: str, contexts_dir: Path = CONTEXTS_DIR, chain_id: int | None = None) -> DeployContext:
    """The snapshot for `name`, plus `contexts/<name>.json` when it exists. `chain_id` (the
    strategy's) is used when the overrides file does not set one."""
    path = contexts_dir / f"{name}.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    snapshot = load_snapshot(data.get("ipor_abi_deployment") or deployment_for(name), contexts_dir / "ipor-abi")
    return DeployContext(raw=data, name=name, snapshot=snapshot, chain_id_hint=chain_id)
