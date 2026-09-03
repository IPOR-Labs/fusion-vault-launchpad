"""Encode substrate values into the bytes32 list passed to `grantMarketSubstrates`.

Dispatch by `encoding` field on the substrate JSON entry. Each encoder takes the
raw list of `values` and returns a list[bytes] (each exactly 32 bytes).
"""
from __future__ import annotations

from eth_abi import encode as abi_encode
from eth_utils import to_bytes
from web3 import Web3


def _to_bytes32(hex_str: str) -> bytes:
    b = to_bytes(hexstr=hex_str)
    if len(b) > 32:
        raise ValueError(f"hex value {hex_str} >32 bytes")
    return b.rjust(32, b"\x00")


def _address_to_bytes32(addr: str) -> bytes:
    a = Web3.to_checksum_address(addr)
    return bytes.fromhex(a[2:]).rjust(32, b"\x00")


def encode_morpho_market_id(values: list) -> list[bytes]:
    out = []
    for v in values:
        if not isinstance(v, str):
            raise ValueError(f"morpho_market_id expects hex string, got {type(v)}")
        out.append(_to_bytes32(v))
    return out


def encode_address(values: list) -> list[bytes]:
    return [_address_to_bytes32(v) for v in values]


def encode_raw_bytes32(values: list) -> list[bytes]:
    return [_to_bytes32(v) for v in values]


def encode_odos_substrate(values: list) -> list[bytes]:
    """Odos substrates: Token (kind=0, address) or Slippage (kind=1, bps).

    Encoding (matches OdosSubstrateLib in ipor-fusion):
      uint8 kind | address(20) | uint8 padding... -> packed into bytes32.
    Token: bytes32 = 0x00 || address (left-pad)
    Slippage: bytes32 = 0x01 || uint16 bps (right-pad-zeros)
    """
    out = []
    for entry in values:
        kind = entry["kind"].lower()
        if kind == "token":
            addr = Web3.to_checksum_address(entry["address"])
            payload = bytes.fromhex(addr[2:])  # 20 bytes
            out.append(b"\x00" + payload.rjust(31, b"\x00"))
        elif kind == "slippage":
            bps = int(entry["bps"])
            out.append(b"\x01" + bps.to_bytes(31, "big"))
        else:
            raise ValueError(f"unknown odos substrate kind: {kind}")
    return out


# --- Universal Token Swapper (markets 12 / 1202) -------------------------------------
#
# Three fuse families share the market and DIFFER ONLY in substrate encoding. The
# keeper (UniversalTokenSwapperDetector) resolves the family from the fuse address
# and decodes the words accordingly, so the encoding MUST match the fuse the vault
# actually carries (see deploy/swapper.py for the name -> encoding guard):
#
#   ADDRESS  — legacy SwapFuseUniversalTokenSwapper(Eth/NoSlippage): plain address words
#              (`address` encoding); every granted address is both a token and a target.
#   TYPED    — UniversalTokenSwapperFuse / EthFuse (+ V2 on market 1202), Jan 2026
#              UniversalTokenSwapperSubstrateLib, type in the MOST significant
#              byte: 1 = Token, 2 = Target, 3 = Slippage (uint248 WAD, 1e18 = 100%).
#   SELECTOR — UniversalTokenSwapperWithVerificationFuse (+ V2 on 1202):
#              (uint32(selector) << 224) | uint160(target); a ZERO selector marks a token
#              word (tokenIn / tokenOut / tokensDustToCheck must all be granted).
#
# The pre-2026-09-02 `universal_substrate_sig_validated` encoder packed
# target(20)|selector(4)|flag(1) left-aligned — a layout no deployed fuse reads
# (shipped to weETH Earn / Apex cbBTC, both obsolete). It was removed; there is no
# "signature validation" concept in any swapper fuse.

UNIVERSAL_TYPE_TOKEN = 1
UNIVERSAL_TYPE_TARGET = 2
UNIVERSAL_TYPE_SLIPPAGE = 3
_WAD = 10**18
_ADDR_MASK = (1 << 160) - 1
_UINT248_MAX = (1 << 248) - 1


def _slippage_wad(entry: dict) -> int:
    if "slippage_wad" in entry:
        wad = int(entry["slippage_wad"])
    elif "slippage_bps" in entry:
        wad = int(entry["slippage_bps"]) * 10**14
    else:
        raise ValueError("slippage entry needs slippage_wad or slippage_bps")
    if not 0 < wad <= _WAD:
        raise ValueError(f"slippage must be in (0, 1e18], got {wad}")
    return wad


def encode_universal_typed_substrate(values: list) -> list[bytes]:
    """TYPED family (UniversalTokenSwapperFuse / EthFuse / *V2): per UniversalTokenSwapperSubstrateLib.

    Entries:
      {"kind": "token",    "address": <addr>}                # tokenIn / tokenOut allow-list
      {"kind": "target",   "address": <addr>}                # router / DEX allow-list
      {"kind": "slippage", "slippage_wad": <int>}            # or "slippage_bps"; optional,
                                                              # fuse default 1e16 (1 %) when absent
    bytes32 = type << 248 | uint160(address)   (token / target)
    bytes32 = 3    << 248 | uint248(slippageWad)
    The fuse iterates every word; the LAST slippage word wins, so grant at most one.
    """
    out = []
    n_slippage = 0
    for entry in values:
        kind = entry["kind"].lower()
        if kind in ("token", "target"):
            addr = Web3.to_checksum_address(entry["address"])
            t = UNIVERSAL_TYPE_TOKEN if kind == "token" else UNIVERSAL_TYPE_TARGET
            out.append(((t << 248) | int(addr, 16)).to_bytes(32, "big"))
        elif kind == "slippage":
            n_slippage += 1
            out.append(((UNIVERSAL_TYPE_SLIPPAGE << 248) | _slippage_wad(entry)).to_bytes(32, "big"))
        else:
            raise ValueError(f"unknown universal typed substrate kind: {kind}")
    if n_slippage > 1:
        raise ValueError("at most one slippage substrate — the fuse keeps only the last one")
    return out


def encode_universal_selector_substrate(values: list) -> list[bytes]:
    """SELECTOR family (UniversalTokenSwapperWithVerificationFuse / *V2): per its toBytes32.

    Entries:
      {"kind": "token",  "address": <addr>}                            # zero-selector word
      {"kind": "target", "target": <addr>, "selector": "0x........"}   # exact (target, fn) pair
    bytes32 = (uint32(selector) << 224) | uint160(target)
    The fuse requires tokenIn, tokenOut AND every tokensDustToCheck entry to be granted
    as token words, plus one (target, selector) word per call it executes.
    """
    out = []
    for entry in values:
        kind = entry["kind"].lower()
        if kind == "token":
            addr = Web3.to_checksum_address(entry["address"])
            out.append(int(addr, 16).to_bytes(32, "big"))
        elif kind == "target":
            target = Web3.to_checksum_address(entry["target"])
            selector = entry["selector"]
            sel = bytes.fromhex(selector[2:] if selector.startswith("0x") else selector)
            if len(sel) != 4:
                raise ValueError(f"selector must be 4 bytes, got {len(sel)}")
            if sel == b"\x00" * 4:
                raise ValueError("zero selector is reserved for token words — use kind=token")
            out.append(((int.from_bytes(sel, "big") << 224) | int(target, 16)).to_bytes(32, "big"))
        else:
            raise ValueError(f"unknown universal selector substrate kind: {kind}")
    return out


def decode_universal_typed_substrate(word: bytes) -> dict:
    """Decode one TYPED word; ValueError for a word the typed fuses would ignore/misread
    (unknown type byte — incl. 0, i.e. a plain address word from the legacy encoding —
    or non-zero padding between the type byte and the address)."""
    if len(word) != 32:
        raise ValueError(f"substrate must be 32 bytes, got {len(word)}")
    w = int.from_bytes(word, "big")
    t = w >> 248
    if t in (UNIVERSAL_TYPE_TOKEN, UNIVERSAL_TYPE_TARGET):
        if (w >> 160) & ((1 << 88) - 1):
            raise ValueError("non-zero padding between type byte and address (left-aligned word?)")
        return {"kind": "token" if t == UNIVERSAL_TYPE_TOKEN else "target",
                "address": Web3.to_checksum_address("0x%040x" % (w & _ADDR_MASK))}
    if t == UNIVERSAL_TYPE_SLIPPAGE:
        wad = w & _UINT248_MAX
        if wad > _WAD:
            raise ValueError(f"slippage {wad} > 100% — the fuse reverts on every swap")
        return {"kind": "slippage", "slippage_wad": wad}
    if t == 0:
        raise ValueError("type byte 0 — plain address (legacy) word, ignored by the typed fuse")
    raise ValueError(f"unknown substrate type {t}")


def decode_universal_selector_substrate(word: bytes) -> dict:
    """Decode one SELECTOR word; ValueError when the 8 bytes between selector and target
    are non-zero (the fuse would read a garbage target)."""
    if len(word) != 32:
        raise ValueError(f"substrate must be 32 bytes, got {len(word)}")
    w = int.from_bytes(word, "big")
    if (w >> 160) & ((1 << 64) - 1):
        raise ValueError("non-zero padding between selector and target (left-aligned / sig_validated word?)")
    sel = w >> 224
    addr = Web3.to_checksum_address("0x%040x" % (w & _ADDR_MASK))
    if sel == 0:
        return {"kind": "token", "address": addr}
    return {"kind": "target", "target": addr, "selector": "0x%08x" % sel}


def encode_euler_substrate(values: list) -> list[bytes]:
    """Euler V2 substrate: (eulerVault, isCollateral, canBorrow, subAccounts).

    Layout per EulerFuseLib.substrateToBytes32:
      address << 96 | isCollateral << 88 | canBorrow << 80 | subAccounts << 72
    """
    out = []
    for entry in values:
        vault = Web3.to_checksum_address(entry["euler_vault"])
        is_collateral = bool(entry.get("is_collateral", False))
        can_borrow = bool(entry.get("can_borrow", False))
        sub_accounts = int(entry.get("sub_accounts", 0))
        if not 0 <= sub_accounts <= 255:
            raise ValueError(f"sub_accounts must fit in one byte, got {sub_accounts}")
        packed = (
            (int(vault, 16) << 96)
            | ((1 if is_collateral else 0) << 88)
            | ((1 if can_borrow else 0) << 80)
            | (sub_accounts << 72)
        )
        out.append(packed.to_bytes(32, "big"))
    return out


def encode_aave_v4_substrate(values: list) -> list[bytes]:
    """Aave V4 reserve substrate (AaveV4SubstrateLib, "improved" fuses 2026-08-26+).

    One entry = one reserve the vault may touch:
      {"spoke": <Spoke addr>, "reserve_id": <uint32>, "is_collateral": bool, "can_borrow": bool}

    bytes32 layout (README "Substrate Configuration"):
      bits 255..248 type flag = 1 (Reserve) | 247..88 spoke | 87..56 reserveId | 55..48 flags
      flags: bit0 = isCollateral, bit1 = canBorrow; bits 47..0 reserved, must be zero.
    Supply-only lending = flags 0. The pre-2026-08-26 Asset/Spoke words are NOT accepted by the
    deployed fuses (non-canonical -> ignored by lookups and by the balance fuse).
    """
    out = []
    for entry in values:
        spoke = Web3.to_checksum_address(entry["spoke"])
        reserve_id = int(entry["reserve_id"])
        if not 0 <= reserve_id <= 0xFFFFFFFF:
            raise ValueError(f"reserve_id must fit uint32, got {reserve_id}")
        flags = (1 if entry.get("is_collateral", False) else 0) | (2 if entry.get("can_borrow", False) else 0)
        word = (1 << 248) | (int(spoke, 16) << 88) | (reserve_id << 56) | (flags << 48)
        out.append(word.to_bytes(32, "big"))
    return out

AAVE_V4_TYPE_RESERVE = 1
AAVE_V4_KNOWN_FLAGS = 0b11


def decode_aave_v4_substrate(word: bytes) -> dict:
    """Decode one Aave V4 Reserve word. Raises ValueError for a NON-CANONICAL word —
    i.e. one the deployed fuses would silently ignore (wrong type flag, unknown flag
    bits, non-zero reserved bits 47..0). This is the check that catches the
    deprecated Asset/Spoke encoding a production vault shipped with (2026-09-01)."""
    if len(word) != 32:
        raise ValueError(f"substrate must be 32 bytes, got {len(word)}")
    w = int.from_bytes(word, "big")
    kind = w >> 248
    if kind != AAVE_V4_TYPE_RESERVE:
        raise ValueError(f"type flag {kind} != 1 (Reserve) — deprecated Asset/Spoke encoding or garbage")
    flags = (w >> 48) & 0xFF
    if flags & ~AAVE_V4_KNOWN_FLAGS:
        raise ValueError(f"unknown flag bits set: 0x{flags:02x}")
    if w & ((1 << 48) - 1):
        raise ValueError("reserved bits 47..0 are non-zero (left-aligned / hand-rolled word?)")
    return {
        "spoke": Web3.to_checksum_address("0x%040x" % ((w >> 88) & ((1 << 160) - 1))),
        "reserve_id": (w >> 56) & 0xFFFFFFFF,
        "is_collateral": bool(flags & 1),
        "can_borrow": bool(flags & 2),
    }


def canonical_check(encoding: str, words: list[bytes]) -> list[str]:
    """Return human-readable problems for on-chain substrate words of a typed
    encoding (empty list = all canonical). Only encodings with a strict on-chain
    layout are checked; others pass through."""
    decoder = _CANONICAL_DECODERS.get(encoding)
    if decoder is None:
        return []
    problems = []
    for w in words:
        try:
            decoder(bytes(w))
        except ValueError as e:
            problems.append(f"0x{bytes(w).hex()}: {e}")
    return problems


_CANONICAL_DECODERS = {
    "aave_v4_substrate": decode_aave_v4_substrate,
    "universal_typed_substrate": decode_universal_typed_substrate,
    "universal_selector_substrate": decode_universal_selector_substrate,
}


_DISPATCH = {
    "morpho_market_id": encode_morpho_market_id,
    "address": encode_address,
    "raw_bytes32": encode_raw_bytes32,
    "odos_substrate": encode_odos_substrate,
    "universal_typed_substrate": encode_universal_typed_substrate,
    "universal_selector_substrate": encode_universal_selector_substrate,
    "euler_substrate": encode_euler_substrate,
    "aave_v4_substrate": encode_aave_v4_substrate,
}


def encode_substrates(encoding: str, values: list) -> list[bytes]:
    if encoding not in _DISPATCH:
        raise KeyError(f"unknown substrate encoding: {encoding}")
    return _DISPATCH[encoding](values)
