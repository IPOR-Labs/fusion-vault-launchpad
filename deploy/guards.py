"""Pre-broadcast safety guards. Pure functions; unit-tested without a chain.

These exist because the same pipeline that rehearses on a local fork also
broadcasts to a live chain, and the difference between the two is one flag.
"""
from __future__ import annotations

from typing import Any, Iterable

# The first ten accounts every anvil / hardhat node funds by default. Their
# private keys are public; any role, whitelist or fee recipient pointing at one
# of them on a live chain hands control of the vault to anyone.
ANVIL_DEFAULT_ACCOUNTS: frozenset[str] = frozenset(a.lower() for a in (
    "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
    "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
    "0x3C44CdDdB6a900fa2b585dd299e03d18d4beD2e2",
    "0x90F79bf6EB2c4f870365E785982E1f101E93b906",
    "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65",
    "0x9965507D1a55bcC2695C58ba16FB37d819B0A4dc",
    "0x976EA74026E726554dB657fA54763abd0C3a0aa9",
    "0x14dC79964da2C08b23698B3D3cc7Ca32193d9955",
    "0x23618e81E3f5cdF7f54C3d65f7FBc0aBf5B21E8f",
    "0xa0Ee7A142d267C1f36714E4a8F75612F20a79720",
))

PUBLIC_RPC_HOSTS = ("publicnode.com", "llamarpc.com", "ankr.com", "cloudflare-eth.com",
                    "drpc.org", "1rpc.io", "blockpi.network", "mainnet.base.org")

LOCAL_NODE_MARKERS = ("anvil", "hardhat", "foundry")


def is_local_node(client_version: str) -> bool:
    """True when `web3_clientVersion` identifies a local development node (a fork)."""
    v = (client_version or "").lower()
    return any(m in v for m in LOCAL_NODE_MARKERS)


def is_public_rpc(rpc_url: str) -> bool:
    host = rpc_url.split("//")[-1].split("/")[0].lower()
    return any(h in host for h in PUBLIC_RPC_HOSTS)


def warn_public_rpc(rpc_url: str) -> None:
    """Broadcast hygiene: a public RPC can confirm so slowly that the client-side timeout
    kills the run mid-sequence, and later hand-sent transactions race nonces."""
    if is_public_rpc(rpc_url):
        host = rpc_url.split("//")[-1].split("/")[0].lower()
        print(f"⚠️  RPC_URL points at a PUBLIC endpoint ({host}). For --broadcast use a private RPC and run "
              f"detached (nohup / background) — public nodes stall confirmations and race nonces. "
              f"If a run is interrupted, re-run WITHOUT --force-restart: completed steps are skipped from state.")


def _addresses_in_config(cfg: dict[str, Any]) -> Iterable[tuple[str, str]]:
    """Yield (json_path, address) for every account that ends up holding power or funds."""
    vault = cfg.get("vault", {})
    if vault.get("initial_owner_override"):
        yield "vault.initial_owner_override", vault["initial_owner_override"]
    for i, g in enumerate(cfg.get("roles", {}).get("grants", [])):
        yield f"roles.grants[{i}].account", g.get("account", "")
    for i, a in enumerate(cfg.get("whitelist", {}).get("initial_accounts", [])):
        yield f"whitelist.initial_accounts[{i}]", a
    for i, r in enumerate(cfg.get("fees", {}).get("recipients", [])):
        yield f"fees.recipients[{i}].address", r.get("address", "")


def placeholder_accounts_in_config(cfg: dict[str, Any]) -> list[str]:
    """JSON paths whose address is a well-known test account. Must be empty before a live broadcast."""
    return [path for path, addr in _addresses_in_config(cfg) if addr.lower() in ANVIL_DEFAULT_ACCOUNTS]


def live_broadcast_problems(cfg: dict[str, Any], signer: str, client_version: str) -> list[str]:
    """Hard-fail reasons for a --broadcast against a node that is not a local fork."""
    if is_local_node(client_version):
        return []
    problems = []
    if signer and signer.lower() in ANVIL_DEFAULT_ACCOUNTS:
        problems.append(f"the signer {signer} is a well-known test account with a public private key")
    for path in placeholder_accounts_in_config(cfg):
        problems.append(f"{path} is a well-known test account (anvil default); replace it with a real address")
    return problems
