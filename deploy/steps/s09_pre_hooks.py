"""Step 09: setPreHookImplementations(bytes4[], address[], bytes32[][]) — raw call on PlasmaVault."""
from __future__ import annotations

from eth_abi import encode as abi_encode
from eth_utils import function_signature_to_4byte_selector

from deploy.encoders.substrates import _to_bytes32

NAME = "09_pre_hooks"


def _selector(sig: str) -> bytes:
    return function_signature_to_4byte_selector(sig)


def _build_substrates(name: str, params: dict) -> list[bytes]:
    if name == "ExchangeRateValidatorPreHook":
        # ValidatorData packed: uint120 threshold | uint128 exchange_rate, prefixed with HookType.VALIDATOR (=2).
        threshold = int(params.get("threshold_bps", 0)) * 10**14  # bps -> 1e18 fraction
        exchange_rate = int(params.get("exchange_rate", 0))
        # bytes31 data: first 120 bits threshold (15 bytes), then 128 bits rate (16 bytes)
        data31 = threshold.to_bytes(15, "big") + exchange_rate.to_bytes(16, "big")
        substrate = bytes([2]) + data31  # HookType=VALIDATOR + data
        return [substrate.ljust(32, b"\x00")]
    return []


def run(cfg, deploy_ctx, session, instance, state, broadcast):
    if state.has_step(NAME):
        print(f"[{NAME}] already done — skipping")
        return
    hooks = cfg.raw.get("pre_hooks", [])
    if not hooks:
        print(f"[{NAME}] no pre-hooks declared")
        state.record(NAME)
        return

    selectors = []
    implementations = []
    substrates_per_hook: list[list[bytes]] = []
    for h in hooks:
        sel_hex = h["selector"]
        sel_bytes = bytes.fromhex(sel_hex[2:] if sel_hex.startswith("0x") else sel_hex)
        if len(sel_bytes) != 4:
            raise ValueError(f"pre_hook selector must be 4 bytes, got {len(sel_bytes)} for {h['name']}")
        selectors.append(sel_bytes)
        implementations.append(deploy_ctx.pre_hook(h["name"]))
        substrates_per_hook.append(_build_substrates(h["name"], h.get("params", {})))
        print(f"[{NAME}] hook={h['name']} sel={sel_hex} impl={implementations[-1]} substrates={len(substrates_per_hook[-1])}")

    calldata = _selector("setPreHookImplementations(bytes4[],address[],bytes32[][])") + abi_encode(
        ["bytes4[]", "address[]", "bytes32[][]"],
        [selectors, implementations, substrates_per_hook],
    )
    rec = session.recorder.add(
        NAME, action="setPreHookImplementations", key=",".join(h["name"] for h in hooks),
        target=instance["plasma_vault"],
        function="setPreHookImplementations(bytes4[],address[],bytes32[][])",
        args={"hooks": [{"name": h["name"], "selector": h["selector"]} for h in hooks]},
        calldata="0x" + calldata.hex(),
    )
    if not broadcast:
        return
    receipt = session.ctx.send(instance["plasma_vault"], calldata)
    tx_hash = receipt["transactionHash"].hex()
    print(f"[{NAME}] tx {tx_hash} gas={receipt['gasUsed']}")
    rec.executed = True
    rec.tx_hash = tx_hash
    rec.gas_used = int(receipt["gasUsed"])
    state.record(NAME, tx_hashes=[tx_hash])
