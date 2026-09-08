"""Callback handlers — the wiring a flash-loan strategy needs and every other
config check misses.

A Morpho flash loan calls back into the vault (`onMorphoFlashLoan`) while a fuse
is executing. The PlasmaVault routes that call through its callback-handler
registry (`CallbackHandlerLib`): the handler registered for
`(sender, selector)` turns the callback into the nested fuse actions and the
repayment approval. With no handler registered the vault reverts
`HandlerNotFound()` (`0x4bf4de4e`) on the FIRST flash loan — after every fuse,
substrate, balance fuse and dependency edge verified green, because none of
those touch the registry.

This module is pure (no chain, no SDK) so the loader can enforce the rule and
the unit suite can pin it:

  * `REQUIRED_CALLBACKS` — which declared fuse needs which handler entry. Only
    the pairs verified on-chain are listed; add a row when you verify a new one.
  * `callback_problems(...)` — cross-check `fuses[]` against `callback_handlers[]`.
  * `storage_slot_for(...)` — where the vault stores the handler, so the step can
    skip an entry that is already set and the verifier can read it back without
    a getter (the contract exposes none).
"""
from __future__ import annotations

import re

from eth_abi import encode as abi_encode
from eth_utils import keccak

# Deployed fuse (context / ipor-abi name) -> the (sender ref, callback signature,
# handler name) it needs on the vault. `sender_ref` is a top-level context key
# holding the address that will call the vault back. Verified 2026-09-08: a Base
# fork clone with MorphoFlashLoanFuse and this entry executed a full
# flash-loan -> swap -> collateral -> borrow loop; without the entry the same
# batch reverts HandlerNotFound() inside Morpho.flashLoan.
REQUIRED_CALLBACKS: dict[str, tuple[str, str, str]] = {
    "MorphoFlashLoanFuse": ("morpho_blue", "onMorphoFlashLoan(uint256,bytes)", "CallbackHandlerMorpho"),
}

HANDLER_NOT_FOUND_SELECTOR = "0x4bf4de4e"  # keccak256("HandlerNotFound()")[:4]

_SIG = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\([A-Za-z0-9_\[\],]*\)$")


def selector(signature: str) -> bytes:
    """4-byte selector of a canonical function signature, e.g. `onMorphoFlashLoan(uint256,bytes)`."""
    if not _SIG.fullmatch(signature or ""):
        raise ValueError(f"not a canonical function signature: {signature!r} (no spaces, no parameter names)")
    return keccak(text=signature)[:4]


def signature_problems(entries: list[dict]) -> list[str]:
    """Pure: malformed `callback_handlers[]` entries (schema-valid but unusable)."""
    problems = []
    for i, e in enumerate(entries):
        sig = e.get("signature", "")
        if not _SIG.fullmatch(sig):
            problems.append(f"callback_handlers[{i}].signature {sig!r} is not canonical "
                            f"(expected e.g. 'onMorphoFlashLoan(uint256,bytes)')")
    return problems


def callback_problems(fuse_names: list[str], entries: list[dict]) -> list[str]:
    """Pure: every declared fuse in `REQUIRED_CALLBACKS` must have its handler entry.

    Returns human-readable problems; empty list = the flash-loan path is wired.
    An entry for a fuse that is NOT declared is allowed (harmless, and a vault
    may register handlers ahead of adding the fuse); it is not flagged here.
    """
    problems = signature_problems(entries)
    have = {(e.get("handler"), e.get("sender"), e.get("signature")) for e in entries}
    for name in fuse_names:
        req = REQUIRED_CALLBACKS.get(name)
        if req is None:
            continue
        sender_ref, sig, handler = req
        if (handler, sender_ref, sig) not in have:
            problems.append(
                f"{name} needs callback_handlers entry "
                f"{{handler: {handler!r}, sender: {sender_ref!r}, signature: {sig!r}}} — "
                f"without it the first flash loan reverts HandlerNotFound() ({HANDLER_NOT_FOUND_SELECTOR})"
            )
    return problems


# --- storage layout (PlasmaVaultStorageLib / CallbackHandlerLib) -----------------------
# struct CallbackHandler { mapping(bytes32 => address) callbackHandler; } lives at
#   S = keccak256(abi.encode(uint256(keccak256("io.ipor.callbackHandler")) - 1)) & ~0xff
# and the mapping is its first (and only) member, so a value sits at
#   keccak256(abi.encode(key, S)) with key = keccak256(abi.encodePacked(sender, selector)).

def _base_slot() -> int:
    inner = int.from_bytes(keccak(text="io.ipor.callbackHandler"), "big") - 1
    outer = int.from_bytes(keccak(abi_encode(["uint256"], [inner])), "big")
    return outer & ~0xFF


CALLBACK_HANDLER_BASE_SLOT: int = _base_slot()


def mapping_key(sender: str, signature: str) -> bytes:
    """`keccak256(abi.encodePacked(sender, msg.sig))` — the registry key the vault looks up."""
    sender_bytes = bytes.fromhex(sender.removeprefix("0x"))
    if len(sender_bytes) != 20:
        raise ValueError(f"sender must be a 20-byte address, got {sender!r}")
    return keccak(sender_bytes + selector(signature))


def storage_slot_for(sender: str, signature: str) -> int:
    """Storage slot holding the handler address registered for `(sender, signature)`."""
    key = mapping_key(sender, signature)
    return int.from_bytes(keccak(abi_encode(["bytes32", "uint256"], [key, CALLBACK_HANDLER_BASE_SLOT])), "big")


def handler_from_slot_word(word: bytes) -> str:
    """Decode an `eth_getStorageAt` word into a checksum-free lowercase address ('0x000…0' = unset)."""
    w = bytes(word).rjust(32, b"\x00")
    return "0x" + w[-20:].hex()
