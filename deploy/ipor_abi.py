"""Vendored snapshots of IPOR-Labs/ipor-abi, the registry of deployed IPOR Fusion contracts.

Chain contexts no longer carry contract addresses. They name contracts by their ipor-abi
key (`IporFusionFactoryProxy`, `CallbackHandlerMorpho`, ...) and the address comes from
`contexts/ipor-abi/<deployment>.json`, a copy of `mainnet/<deployment>/addresses.json`
pinned to an ipor-abi commit. `tools/refresh_ipor_abi.py` (`make ipor-abi`) refreshes the
copies and prints what changed. Runs never fetch ipor-abi, so a run is reproducible and
the tests stay offline.

The on-chain FuseWhitelist stays the authority for fuses (`deploy/fuse_resolver.py`);
ipor-abi is the authority for everything the whitelist does not track (factory, oracle
middleware, price-feed factories, callback handlers, pre-hooks) and the cross-check for
the rest (`deploy/address_book.py`).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from web3 import Web3

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "contexts" / "ipor-abi"
REPOSITORY = "IPOR-Labs/ipor-abi"


def is_address(value) -> bool:
    return isinstance(value, str) and value.startswith("0x") and len(value) == 42


@dataclass(frozen=True)
class Snapshot:
    deployment: str
    addresses: dict[str, str]
    source: dict = field(default_factory=dict)   # repository, path, commit, fetched

    def get(self, key: str):
        """Checksum address for an ipor-abi key, None when the key is absent or not an address."""
        val = self.addresses.get(key)
        return Web3.to_checksum_address(val) if is_address(val) else None

    def require(self, key: str, what: str = "") -> str:
        addr = self.get(key)
        if addr is None:
            raise KeyError(f"{what or key}: key '{key}' not in ipor-abi {self.deployment} "
                           f"(snapshot commit {self.commit[:10]}); run `make ipor-abi` or fix the context")
        return addr

    def keys_for(self, address: str) -> list[str]:
        """Every ipor-abi key that names this address (reverse lookup for reports)."""
        a = address.lower()
        return sorted(k for k, v in self.addresses.items() if is_address(v) and v.lower() == a)

    @property
    def commit(self) -> str:
        return str(self.source.get("commit", ""))


def snapshot_document(deployment: str, addresses: dict, commit: str, fetched: str) -> dict:
    """The on-disk shape of a vendored snapshot."""
    return {
        "source": {"repository": REPOSITORY, "path": f"mainnet/{deployment}/addresses.json",
                   "commit": commit, "fetched": fetched},
        "addresses": {k: addresses[k] for k in sorted(addresses)},
    }


def load_snapshot(deployment: str, directory: Path = SNAPSHOT_DIR) -> Snapshot:
    p = directory / f"{deployment}.json"
    if not p.exists():
        raise FileNotFoundError(f"no ipor-abi snapshot for '{deployment}' at {p}; run `make ipor-abi`")
    doc = json.loads(p.read_text())
    return Snapshot(deployment=deployment, addresses=dict(doc["addresses"]), source=dict(doc.get("source", {})))


def snapshot_diff(old: dict, new: dict) -> tuple[list[str], list[str], list[str]]:
    """(added, removed, changed) keys between two address maps."""
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(k for k in set(old) & set(new) if str(old[k]).lower() != str(new[k]).lower())
    return added, removed, changed
