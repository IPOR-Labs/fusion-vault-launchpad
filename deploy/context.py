"""Per-chain deploy context loader.

A context names IPOR contracts by their ipor-abi key; the addresses come from the vendored
ipor-abi snapshot (`deploy/ipor_abi.py`). Fuses are resolved on the FuseWhitelist before any
step runs (`deploy/fuse_resolver.py`) and installed as overrides; the ipor-abi address is the
fallback and the cross-check (`deploy/address_book.py`). A literal address appears only as a
`pins` entry (with the reason it overrides the registries), under `external_addresses`
(third-party contracts with their source) or under `tokens`.
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


@dataclass(slots=True)
class DeployContext:
    raw: dict[str, Any]
    name: str
    snapshot: Snapshot
    _fuse_overrides: dict[str, str] = field(default_factory=dict)

    # --- registry lookups ---------------------------------------------------------

    def _contract(self, ref: str) -> ChecksumAddress:
        key = self.raw.get("contracts", {}).get(ref)
        if not key:
            raise KeyError(f"context '{self.name}' names no '{ref}' under contracts")
        return self.snapshot.require(key, f"{self.name}.contracts.{ref}")

    def pin(self, name: str) -> ChecksumAddress | None:
        p = self.raw.get("pins", {}).get(name)
        return Web3.to_checksum_address(p["address"]) if p else None

    def pins(self) -> dict[str, ChecksumAddress]:
        return {n: Web3.to_checksum_address(p["address"]) for n, p in self.raw.get("pins", {}).items()}

    def registry_fuse(self, name: str) -> ChecksumAddress | None:
        """What ipor-abi says for a fuse name (via the context's key), None when it has no entry."""
        key = self.raw.get("fuses", {}).get(name)
        return self.snapshot.get(key) if key else None

    # --- public API used by the steps ---------------------------------------------

    @property
    def chain_id(self) -> int:
        return int(self.raw["chain_id"])

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
        key = self.raw.get("contracts", {}).get("middleware_owner")
        return self.snapshot.get(key) if key else None

    @property
    def fuse_whitelist(self) -> ChecksumAddress:
        """On-chain FuseWhitelist: the authority for which fuse address is current."""
        return self._contract("fuse_whitelist")

    def fuse(self, name: str) -> ChecksumAddress:
        """Address for a fuse name: the FuseWhitelist resolution (installed as an override by
        `deploy/fuse_resolver.py`), else a pin, else the ipor-abi entry."""
        addr = self._fuse_overrides.get(name) or self.pin(name) or self.registry_fuse(name)
        if not addr:
            raise KeyError(f"fuse '{name}' not resolved on the FuseWhitelist, not pinned and not in ipor-abi "
                           f"for context '{self.name}'")
        return Web3.to_checksum_address(addr)

    def set_fuse_override(self, name: str, address: str) -> None:
        self._fuse_overrides[name] = Web3.to_checksum_address(address)

    def standard_fuses(self) -> list[ChecksumAddress]:
        """Fuses the FusionFactory injects into every vault (e.g. BurnRequestFeeFuse), oldest first.

        Always present on-chain but never declared in a strategy JSON, so the
        verifier treats them as expected rather than 'extra' (the request-fee
        burn is part of the standard withdraw/redemption flow)."""
        return [self.snapshot.require(k, f"{self.name}.standard_fuses") for k in self.raw.get("standard_fuses", [])]

    def pre_hook(self, name: str) -> ChecksumAddress:
        key = self.raw.get("pre_hooks", {}).get(name)
        addr = self.pin(name) or (self.snapshot.get(key) if key else None)
        if not addr:
            raise KeyError(f"pre-hook '{name}' missing from context '{self.name}' (add its ipor-abi key under pre_hooks)")
        return addr

    def callback_handler(self, name: str) -> ChecksumAddress:
        """Stateless callback-handler contract (e.g. CallbackHandlerMorpho) by context name."""
        key = self.raw.get("callback_handlers", {}).get(name)
        if not key:
            raise KeyError(f"callback handler '{name}' missing from context '{self.name}' "
                           f"(add its ipor-abi key under callback_handlers)")
        return self.snapshot.require(key, f"{self.name}.callback_handlers.{name}")

    def external(self, name: str) -> ChecksumAddress | None:
        e = self.raw.get("external_addresses", {}).get(name)
        return Web3.to_checksum_address(e["address"]) if e else None

    def token(self, symbol: str) -> ChecksumAddress:
        return Web3.to_checksum_address(self.raw["tokens"][symbol])

    def resolve_address(self, ref: str) -> ChecksumAddress:
        """An address literal, or a context name: a contract (`morpho_blue`), an external
        address (`aave_v3_pool`) or a token symbol."""
        if is_address(ref):
            return Web3.to_checksum_address(ref)
        if ref in self.raw.get("contracts", {}):
            return self._contract(ref)
        if ref in self.raw.get("external_addresses", {}):
            return self.external(ref)
        if ref in self.raw.get("tokens", {}):
            return self.token(ref)
        raise KeyError(f"'{ref}' is neither an address nor a contract, external address or token in context '{self.name}'")

    def price_feed_factory(self, name: str) -> ChecksumAddress:
        key = self.raw.get("price_feed_factories", {}).get(name)
        if not key:
            raise KeyError(f"price-feed-factory '{name}' missing from context '{self.name}'")
        return self.snapshot.require(key, f"{self.name}.price_feed_factories.{name}")

    def known_addresses(self) -> set[str]:
        """Every address this context resolves to (lower-case), for cross-checks such as spec-lint."""
        out: set[str] = set()
        for ref in self.raw.get("contracts", {}):
            try:
                out.add(self._contract(ref).lower())
            except KeyError:
                pass
        for cat in ("fuses", "pre_hooks", "callback_handlers", "price_feed_factories"):
            for key in self.raw.get(cat, {}).values():
                if key and self.snapshot.get(key):
                    out.add(self.snapshot.get(key).lower())
        out |= {a.lower() for a in self.standard_fuses()}
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


def load_context(name: str, contexts_dir: Path = CONTEXTS_DIR) -> DeployContext:
    data = json.loads((contexts_dir / f"{name}.json").read_text())
    snapshot = load_snapshot(data["ipor_abi_deployment"], contexts_dir / "ipor-abi")
    return DeployContext(raw=data, name=name, snapshot=snapshot)
