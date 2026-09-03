"""Pure fuse-set classification — no chain reads, no SDK import.

Lives in its own module (not `verification.py`) so it stays importable without
the `ipor_fusion` SDK — the verifier imports the SDK at module top, but the test
suite + CI are deliberately SDK-free. Shared by `deploy.verification`.
"""
from __future__ import annotations


def classify_fuses(
    on_chain: set[str], declared: set[str], standard: set[str]
) -> tuple[set[str], set[str], set[str]]:
    """Split on-chain fuses against the config. Pure — no chain reads.

    Returns ``(missing, unexpected, injected)`` (all lowercased):
      * ``missing``    — declared in the strategy JSON but absent on-chain → ❌.
      * ``unexpected`` — on-chain but neither declared nor a standard factory
                         fuse → ⚠️ (a genuinely unforeseen fuse worth surfacing).
      * ``injected``   — on-chain standard factory fuse (e.g. BurnRequestFeeFuse),
                         expected and benign.
    """
    on_chain = {a.lower() for a in on_chain}
    declared = {a.lower() for a in declared}
    standard = {a.lower() for a in standard}
    missing = declared - on_chain
    unexpected = on_chain - declared - standard
    injected = on_chain & standard
    return missing, unexpected, injected


def plan_standard_fuse_upgrade(on_chain: set[str], standard: list[str]) -> tuple[list[str], list[str]]:
    """Which standard (factory-injected) fuses to add / remove so the vault runs the
    LATEST version. ``standard`` is the context list ordered oldest → newest; the
    last entry is the version every new vault should carry. Pure — no chain reads.

    Returns ``(to_add, to_remove)`` (checksum-agnostic, lowercased). The factory
    injects the OLD BurnRequestFeeFuse at clone time (observed 2026-09-01: V1
    0x79e8… injected while V2 0x6Deb… is the whitelisted latest), so a post-clone
    upgrade step is required until the factory's standard list is updated.
    """
    if not standard:
        return [], []
    on_chain = {a.lower() for a in on_chain}
    std = [a.lower() for a in standard]
    latest = std[-1]
    to_add = [] if latest in on_chain else [latest]
    to_remove = [a for a in std[:-1] if a in on_chain]
    return to_add, to_remove


# Minimum ``instantWithdraw`` params per supply-fuse family (params[0] = amount slot).
# The deployed fuse reverts (e.g. AaveV4SupplyFuseInvalidParams) or misreads the
# venue if the entry is shorter — a production Aave V4 queue entry once shipped with 4
# instead of 5 and every instant withdraw through the queue reverted (2026-09-01).
QUEUE_MIN_PARAMS = {
    ("AaveV3SupplyFuse", "SparkLendSupplyFuse", "SupplyFuseAaveV3"): 2,   # [amount, asset]
    ("MorphoSupplyFuse", "SupplyFuseMorpho"): 2,                        # [amount, marketId]
    ("Erc4626SupplyFuse", "SupplyFuseErc4626"): 2,                     # [amount, vault]
    ("SupplyFuseEulerV2", "EulerV2SupplyFuse"): 3,                     # [amount, vault, subAccount]
    ("SupplyFuseAaveV4", "AaveV4SupplyFuse"): 5,                       # [amount, asset, spoke, reserveId, minAmount]
}


def queue_param_problems(order: list[dict]) -> list[str]:
    """Pure: entries of ``instant_withdrawal.order`` whose params are shorter than
    the deployed fuse family requires. Empty list = OK; unknown families pass."""
    problems = []
    for entry in order:
        name = entry.get("fuse", "")
        n = len(entry.get("params", []))
        for names, need in QUEUE_MIN_PARAMS.items():
            if any(name.startswith(x) for x in names) and n < need:
                problems.append(f"{name}: {n} params, fuse needs >= {need}")
    return problems
