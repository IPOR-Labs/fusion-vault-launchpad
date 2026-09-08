"""Step 03b: register callback handlers — updateCallbackHandler(address,address,bytes4).

Needed by every fuse that makes an external protocol call back into the vault
mid-execution (Morpho flash loans today). The loader already refuses a strategy
that declares such a fuse without the matching `callback_handlers[]` entry
(deploy/callbacks.py); this step writes the entries and skips the ones the vault
already holds, read straight from storage (the contract has no getter).
"""
from __future__ import annotations

from eth_abi import encode as abi_encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from deploy.callbacks import handler_from_slot_word, selector, storage_slot_for

NAME = "03b_callback_handlers"
FUNCTION = "updateCallbackHandler(address,address,bytes4)"


def read_registered_handler(w3, vault: str, sender: str, signature: str) -> str:
    """Lowercase handler address registered on `vault` for (sender, signature); zero address if none."""
    word = w3.eth.get_storage_at(Web3.to_checksum_address(vault), storage_slot_for(sender, signature))
    return handler_from_slot_word(word)


def run(cfg, deploy_ctx, session, instance, state, broadcast):
    if state.has_step(NAME):
        print(f"[{NAME}] already done — skipping")
        return
    entries = cfg.raw.get("callback_handlers", [])
    if not entries:
        print(f"[{NAME}] no callback handlers declared")
        if broadcast:
            state.record(NAME)
        return
    vault_addr = instance["plasma_vault"]
    w3 = session.ctx.web3
    tx_hashes = []
    for e in entries:
        handler = deploy_ctx.callback_handler(e["handler"])
        sender = deploy_ctx.resolve_address(e["sender"])
        sig = e["signature"]
        sel = selector(sig)
        rec = session.recorder.add(
            NAME, action="updateCallbackHandler", key=f"{e['handler']}:{sig}",
            target=vault_addr, function=FUNCTION,
            args={"handler": e["handler"], "handler_address": handler,
                  "sender": e["sender"], "sender_address": sender,
                  "signature": sig, "selector": "0x" + sel.hex()},
        )
        print(f"[{NAME}] {e['handler']} {handler} <- sender {e['sender']} {sender} sig {sig} (0x{sel.hex()})")
        # Idempotency: an existing vault (resume / re-run) may already route this callback.
        # In a dry-run the vault does not exist, so the read returns the zero word.
        try:
            current = read_registered_handler(w3, vault_addr, sender, sig)
        except Exception:
            current = "0x" + "00" * 20
        if current.lower() == handler.lower():
            print(f"[{NAME}]   already registered on-chain — skip")
            rec.skipped = True
            rec.note = "handler already registered"
            continue
        if not broadcast:
            continue
        calldata = function_signature_to_4byte_selector(FUNCTION) + abi_encode(
            ["address", "address", "bytes4"], [handler, sender, sel]
        )
        receipt = session.ctx.send(vault_addr, calldata)
        tx_hash = receipt["transactionHash"].hex()
        tx_hashes.append(tx_hash)
        rec.executed = True
        rec.tx_hash = tx_hash
        rec.gas_used = int(receipt["gasUsed"])
        print(f"[{NAME}]   tx {tx_hash} gas={receipt['gasUsed']}")
    if broadcast:
        state.record(NAME, tx_hashes=tx_hashes)
