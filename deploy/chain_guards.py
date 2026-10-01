"""Checks that keep a run from configuring a vault that does not exist on the chain it talks to.

Two guards, both pure (the caller passes the chain lookups in, so the unit suite stays SDK-free):

- `resume_problems`: before a run resumes from a state file, the recorded clone must be a mined,
  successful transaction on THIS chain, and the vault and its access manager must have code.
  This catches state written by an unsent clone, by an earlier anvil fork (same chain id as the
  live chain), or by a different RPC.
- `price_problem`: a price feed must answer a plausible USD price. A fixed-price feed that answers
  1 with 8 decimals prices a stablecoin at 1e-8 USD; every read-back check still sees a non-zero price.
- `target_problem`: every transaction the pipeline sends goes to a contract. A target without
  code means the pipeline is working from addresses that were never deployed, so the send is refused.
"""
from __future__ import annotations

from typing import Callable

CLONE_STEP = "01_clone"
REQUIRED_CODE = ("plasma_vault", "access_manager")


def resume_problems(state, get_receipt: Callable[[str], dict | None],
                    get_code: Callable[[str], bytes]) -> list[str]:
    """Problems with resuming from `state` on the connected chain; empty when it is safe.
    `get_receipt(hash)` returns the receipt or None when the chain does not know the hash."""
    inst = state.fusion_instance
    if not inst:
        return []
    rec = next((r for r in state.completed_steps if r.step == CLONE_STEP), None)
    if rec is None:
        # an unsent clone's preview: 01_clone discards it and clones again
        return []
    problems = []
    tx = rec.tx_hashes[0] if rec.tx_hashes else None
    if not tx:
        problems.append("the state records the clone without a transaction hash")
    else:
        tx = tx if tx.startswith("0x") else "0x" + tx   # HexBytes.hex() is unprefixed on web3 >= 7
        receipt = get_receipt(tx)
        if receipt is None:
            problems.append(f"the clone transaction {tx} is not on this chain (state from another fork, chain or RPC)")
        elif int(receipt.get("status", 0)) != 1:
            problems.append(f"the clone transaction {tx} reverted on this chain")
    for key in REQUIRED_CODE:
        addr = inst.get(key)
        if not addr or not get_code(addr):
            problems.append(f"{key} {addr} has no code on this chain")
    return problems


def resume_error(state_path, problems: list[str]) -> str:
    return (f"Refusing to resume from {state_path}: it describes a vault this chain does not have.\n  - "
            + "\n  - ".join(problems)
            + "\nNothing was sent. If this state belongs to an old fork or a failed attempt, re-run with "
              "--force-restart to deploy a new vault. If you expected this vault to exist, check RPC_URL.")


def target_problem(to: str | None, code: bytes) -> str | None:
    """Why a transaction to `to` must not be sent, or None."""
    if not to:
        return "the transaction has no target (contract creation is not part of this pipeline)"
    if not code:
        return (f"refusing to send to {to}: no contract there on this chain. The run is working from "
                "addresses that were never deployed; stop and check the state file and RPC_URL.")
    return None


MIN_USD_PRICE = 10**-6        # below any asset a Fusion vault holds; catches 1-wei answers on 8-decimal feeds
MAX_USD_PRICE = 10**7


def price_problem(what: str, answer: int, decimals: int, allow_outside: bool = False) -> str | None:
    """Why a USD price read from a feed or the vault oracle must not be trusted, or None.
    `allow_outside` is the strategy's explicit per-feed opt-out for genuinely tiny or huge prices."""
    if answer <= 0:
        return f"{what} answers {answer}: no usable price"
    usd = answer / 10**decimals
    if allow_outside or MIN_USD_PRICE <= usd <= MAX_USD_PRICE:
        return None
    return (f"{what} prices at {usd:.3g} USD (answer {answer}, {decimals} decimals), outside "
            f"{MIN_USD_PRICE:g}..{MAX_USD_PRICE:g} USD. Wrong feed or wrong decimals; if the price is real, "
            'set "allow_price_outside_sanity": true on this price_feeds entry.')


# ---------------------------------------------------------------- permission audit
IPOR_DAO_ROLE = 4
WHITELIST_ROLE = 800        # depositor allowlist: may deposit, cannot change the vault
ROLE_NAMES = {0: "ADMIN", 1: "OWNER", 2: "GUARDIAN", 3: "TECH_PLASMA_VAULT", 4: "IPOR_DAO", 5: "TECH_CONTEXT_MANAGER",
              6: "TECH_WITHDRAW_MANAGER", 7: "TECH_VAULT_TRANSFER_SHARES", 100: "ATOMIST", 200: "ALPHA", 300: "FUSE_MANAGER",
              301: "PRE_HOOKS_MANAGER", 400: "TECH_PERFORMANCE_FEE_MANAGER", 500: "TECH_MANAGEMENT_FEE_MANAGER",
              600: "CLAIM_REWARDS", 601: "TECH_REWARDS_CLAIM_MANAGER", 700: "TRANSFER_REWARDS", 800: "WHITELIST",
              900: "CONFIG_INSTANT_WITHDRAWAL_FUSES", 901: "WITHDRAW_MANAGER_REQUEST_FEE", 902: "WITHDRAW_MANAGER_WITHDRAW_FEE",
              1000: "UPDATE_MARKETS_BALANCES", 1100: "UPDATE_REWARDS_BALANCE", 1200: "PRICE_ORACLE_MIDDLEWARE_MANAGER"}


def role_holders(events: list[tuple[str, int, str]]) -> set[tuple[int, str]]:
    """Replay RoleGranted / RoleRevoked events, in chain order, into the current (role, account) set.
    Each event is ("granted" | "revoked", role_id, account)."""
    held: set[tuple[int, str]] = set()
    for kind, role, account in events:
        key = (int(role), account.lower())
        if kind == "granted":
            held.add(key)
        else:
            held.discard(key)
    return held


def permission_problems(held: set[tuple[int, str]], spec_accounts: set[str], system_contracts: set[str]) -> tuple[list[str], list[str]]:
    """(fails, notes). Every role holder must be an account the spec names (roles.grants, the
    deployer), one of the vault's own contracts, or the IPOR DAO in its DAO role. WHITELIST holders
    (depositors) are listed, not failed. Anything else is a leftover permission."""
    spec = {a.lower() for a in spec_accounts}
    system = {a.lower() for a in system_contracts}
    fails, notes = [], []
    for role, account in sorted(held):
        name = ROLE_NAMES.get(role, str(role))
        if account in spec or account in system or role == IPOR_DAO_ROLE:
            continue
        if role == WHITELIST_ROLE:
            notes.append(f"{account} may deposit (WHITELIST)")
            continue
        fails.append(f"{account} holds {name} ({role}) but the spec does not grant it")
    return fails, notes
