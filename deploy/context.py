"""Per-chain deploy context loader."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eth_typing import ChecksumAddress
from web3 import Web3

CONTEXTS_DIR = Path(__file__).resolve().parent.parent / "contexts"


@dataclass(slots=True)
class DeployContext:
    raw: dict[str, Any]
    name: str

    @property
    def chain_id(self) -> int:
        return int(self.raw["chain_id"])

    @property
    def fusion_factory(self) -> ChecksumAddress:
        return Web3.to_checksum_address(self.raw["fusion_factory"])

    @property
    def price_oracle_middleware(self) -> ChecksumAddress:
        return Web3.to_checksum_address(self.raw["price_oracle_middleware"])

    @property
    def public_rpc(self) -> str | None:
        """Read-only public endpoint for dry-runs when RPC_URL is unset. Never used for --broadcast."""
        return self.raw.get("public_rpc") or None

    @property
    def middleware_owner(self) -> ChecksumAddress | None:
        """Holder of the manager role on the shared PriceOracleMiddleware (fork impersonation only)."""
        addr = self.raw.get("middleware_owner")
        return Web3.to_checksum_address(addr) if addr else None

    @property
    def fuse_whitelist(self) -> ChecksumAddress:
        """On-chain FuseWhitelist — the authority the fuse map below is only a cache of."""
        addr = self.raw.get("fuse_whitelist")
        if not addr:
            raise KeyError(f"context '{self.name}' has no fuse_whitelist address")
        return Web3.to_checksum_address(addr)

    def fuse(self, name: str) -> ChecksumAddress:
        addr = self.raw["fuses"].get(name)
        if not addr:
            raise KeyError(f"fuse '{name}' missing from context '{self.name}'")
        return Web3.to_checksum_address(addr)

    def standard_fuses(self) -> list[ChecksumAddress]:
        """Fuses the FusionFactory injects into every vault (e.g. BurnRequestFeeFuse).

        Always present on-chain but never declared in a strategy JSON, so the
        verifier treats them as expected rather than 'extra' (the request-fee
        burn is part of the standard withdraw/redemption flow)."""
        return [Web3.to_checksum_address(a) for a in self.raw.get("standard_fuses", [])]

    def pre_hook(self, name: str) -> ChecksumAddress:
        addr = self.raw.get("pre_hooks", {}).get(name) or self.raw["fuses"].get(name)
        if not addr:
            raise KeyError(f"pre-hook '{name}' missing from context '{self.name}'")
        return Web3.to_checksum_address(addr)

    def callback_handler(self, name: str) -> ChecksumAddress:
        """Stateless callback-handler contract (e.g. CallbackHandlerMorpho) by ipor-abi name."""
        addr = self.raw.get("callback_handlers", {}).get(name)
        if not addr:
            raise KeyError(f"callback handler '{name}' missing from context '{self.name}' "
                           f"(add it under callback_handlers from ipor-abi addresses.json)")
        return Web3.to_checksum_address(addr)

    def resolve_address(self, ref: str) -> ChecksumAddress:
        """An address literal, or the name of a top-level context address such as `morpho_blue`."""
        if isinstance(ref, str) and ref.startswith("0x"):
            return Web3.to_checksum_address(ref)
        val = self.raw.get(ref)
        if isinstance(val, str) and val.startswith("0x"):
            return Web3.to_checksum_address(val)
        raise KeyError(f"'{ref}' is neither an address nor a top-level address key in context '{self.name}'")

    def price_feed_factory(self, name: str) -> ChecksumAddress:
        addr = self.raw["price_feed_factories"].get(name)
        if not addr:
            raise KeyError(f"price-feed-factory '{name}' missing from context '{self.name}'")
        return Web3.to_checksum_address(addr)

    def market_id(self, name: str) -> int:
        # Falls back to ipor_fusion.market_ids if not in context.
        markets = self.raw.get("markets", {})
        if name in markets:
            return int(markets[name])
        from ipor_fusion.market_ids import IporFusionMarkets
        if hasattr(IporFusionMarkets, name):
            return int(getattr(IporFusionMarkets, name))
        raise KeyError(f"market '{name}' not defined")


def load_context(name: str) -> DeployContext:
    p = CONTEXTS_DIR / f"{name}.json"
    with p.open() as f:
        data = json.load(f)
    return DeployContext(raw=data, name=name)
