"""deploy.encoders.substrates — golden-byte tests for substrate encoding.

Expected values are built from an independent, obviously-correct expression (not
by re-running the encoder), so the assertions actually pin the byte layout.
"""
import pytest

from deploy.encoders.substrates import encode_substrates, canonical_check

ADDR1 = "0x0000000000000000000000000000000000000001"
SEL = "0xaabbccdd"


def test_address_is_left_padded_to_32():
    (out,) = encode_substrates("address", [ADDR1])
    assert out == bytes(12) + bytes.fromhex(ADDR1[2:])
    assert len(out) == 32


def test_raw_bytes32_right_justified():
    (out,) = encode_substrates("raw_bytes32", ["0x01"])
    assert out == bytes(31) + b"\x01"


def test_morpho_market_id_padded():
    (out,) = encode_substrates("morpho_market_id", ["0xff"])
    assert out == bytes(31) + b"\xff"
    assert len(out) == 32


def test_odos_token_prefix_zero():
    (out,) = encode_substrates("odos_substrate", [{"kind": "token", "address": ADDR1}])
    assert out[0] == 0x00
    assert len(out) == 32
    assert out.endswith(b"\x01")


def test_odos_slippage_prefix_one():
    (out,) = encode_substrates("odos_substrate", [{"kind": "slippage", "bps": 100}])
    assert out[0] == 0x01
    assert out == b"\x01" + (100).to_bytes(31, "big")


# --- Universal Token Swapper: TYPED family (UniversalTokenSwapperSubstrateLib) ---------
# golden words are the Solidity expressions written out by hand:
#   token:    bytes32(uint256(1) << 248) | bytes32(uint256(uint160(addr)))
#   target:   bytes32(uint256(2) << 248) | ...
#   slippage: bytes32(uint256(3) << 248) | bytes32(slippageWad)
from deploy.encoders.substrates import (
    decode_universal_typed_substrate, decode_universal_selector_substrate,
)

ROUTER = "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45"


def test_universal_typed_token_and_target_words():
    tok, tgt = encode_substrates("universal_typed_substrate", [
        {"kind": "token", "address": ADDR1}, {"kind": "target", "address": ROUTER}])
    assert tok == b"\x01" + bytes(11) + bytes.fromhex(ADDR1[2:])
    assert tgt == b"\x02" + bytes(11) + bytes.fromhex(ROUTER[2:])


def test_universal_typed_slippage_word_wad_and_bps():
    (a,) = encode_substrates("universal_typed_substrate", [{"kind": "slippage", "slippage_wad": 5 * 10**15}])
    (b,) = encode_substrates("universal_typed_substrate", [{"kind": "slippage", "slippage_bps": 50}])
    assert a == b == b"\x03" + (5 * 10**15).to_bytes(31, "big")


def test_universal_typed_rejects_two_slippage_words_and_over_100pct():
    with pytest.raises(ValueError):
        encode_substrates("universal_typed_substrate",
                          [{"kind": "slippage", "slippage_bps": 1}, {"kind": "slippage", "slippage_bps": 2}])
    with pytest.raises(ValueError):
        encode_substrates("universal_typed_substrate", [{"kind": "slippage", "slippage_wad": 10**18 + 1}])


def test_universal_typed_decode_roundtrip_and_rejects_legacy_word():
    words = encode_substrates("universal_typed_substrate", [
        {"kind": "token", "address": ADDR1}, {"kind": "target", "address": ROUTER},
        {"kind": "slippage", "slippage_bps": 100}])
    assert [decode_universal_typed_substrate(w) for w in words] == [
        {"kind": "token", "address": ADDR1}, {"kind": "target", "address": ROUTER},
        {"kind": "slippage", "slippage_wad": 10**16}]
    legacy = bytes(12) + bytes.fromhex(ADDR1[2:])          # plain address word (type byte 0)
    with pytest.raises(ValueError):
        decode_universal_typed_substrate(legacy)
    assert canonical_check("universal_typed_substrate", [words[0], legacy]) and \
        canonical_check("universal_typed_substrate", words) == []


# --- Universal Token Swapper: SELECTOR family (WithVerificationFuse.toBytes32) ---------
#   bytes32((uint256(uint32(selector)) << 224) | uint256(uint160(target)))


def test_universal_selector_target_word_matches_solidity_toBytes32():
    (out,) = encode_substrates("universal_selector_substrate",
                               [{"kind": "target", "target": ADDR1, "selector": SEL}])
    assert out == bytes.fromhex(SEL[2:]) + bytes(8) + bytes.fromhex(ADDR1[2:])


def test_universal_selector_token_word_is_plain_address():
    (out,) = encode_substrates("universal_selector_substrate", [{"kind": "token", "address": ADDR1}])
    assert out == bytes(12) + bytes.fromhex(ADDR1[2:])
    assert decode_universal_selector_substrate(out) == {"kind": "token", "address": ADDR1}


def test_universal_selector_rejects_zero_selector_target_and_bad_length():
    with pytest.raises(ValueError):
        encode_substrates("universal_selector_substrate",
                          [{"kind": "target", "target": ADDR1, "selector": "0x00000000"}])
    with pytest.raises(ValueError):
        encode_substrates("universal_selector_substrate",
                          [{"kind": "target", "target": ADDR1, "selector": "0xaabb"}])


def test_universal_selector_canonical_check_catches_the_shipped_sig_validated_layout():
    # the word weETH Earn shipped on-chain (target|selector|flag left-aligned) — 2026-09-02 audit
    shipped = bytes.fromhex("68b3465833fb72a70ecdf485e0e4c7bd8665fc4504e45aaf0000000000000000")
    good = encode_substrates("universal_selector_substrate",
                             [{"kind": "target", "target": ROUTER, "selector": "0x04e45aaf"}])[0]
    assert decode_universal_selector_substrate(good) == {"kind": "target", "target": ROUTER, "selector": "0x04e45aaf"}
    problems = canonical_check("universal_selector_substrate", [good, shipped])
    assert len(problems) == 1 and shipped.hex() in problems[0]


def test_unknown_encoding_raises():
    with pytest.raises(KeyError):
        encode_substrates("nonsense", [])


# --- Aave V4 reserve substrates (AaveV4SubstrateLib: type@248 | spoke@88 | reserveId@56 | flags@48) ---
MAIN_SPOKE = "0x94e7A5dCbE816e498b89aB752661904E2F56c485"
# golden value supplied by the Aave V4 fuse developer (Main spoke, reserve 0, supply-only)
DEV_GOLDEN = bytes.fromhex("0194e7a5dcbe816e498b89ab752661904e2f56c4850000000000000000000000")


def test_aave_v4_reserve_supply_only_matches_developer_golden():
    (out,) = encode_substrates("aave_v4_substrate", [{"spoke": MAIN_SPOKE, "reserve_id": 0}])
    assert out == DEV_GOLDEN
    assert len(out) == 32


def test_aave_v4_reserve_flags_and_reserve_id():
    (out,) = encode_substrates("aave_v4_substrate", [{"spoke": MAIN_SPOKE, "reserve_id": 7, "is_collateral": True, "can_borrow": True}])
    w = int.from_bytes(out, "big")
    assert w >> 248 == 1
    assert (w >> 88) & ((1 << 160) - 1) == int(MAIN_SPOKE, 16)
    assert (w >> 56) & 0xFFFFFFFF == 7
    assert (w >> 48) & 0xFF == 0b11
    assert w & ((1 << 48) - 1) == 0  # reserved bits


def test_aave_v4_reserve_rejects_overflowing_reserve_id():
    with pytest.raises(ValueError):
        encode_substrates("aave_v4_substrate", [{"spoke": MAIN_SPOKE, "reserve_id": 1 << 32}])


# --- canonical decode: the check that would have caught the deprecated Asset/Spoke words ---
from deploy.encoders.substrates import decode_aave_v4_substrate


def test_aave_v4_decode_roundtrip_developer_word():
    d = decode_aave_v4_substrate(DEV_GOLDEN)
    assert d == {"spoke": MAIN_SPOKE, "reserve_id": 0, "is_collateral": False, "can_borrow": False}


def test_aave_v4_decode_rejects_deprecated_asset_and_spoke_words():
    old_asset = b"\x01" + bytes(11) + bytes.fromhex("C02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")   # old Asset(WETH)
    old_spoke = b"\x02" + bytes(11) + bytes.fromhex("bF10BDfE177dE0336aFD7fcCF80A904E15386219")   # old Spoke(EtherFi)
    left_aligned = b"\x01" + bytes.fromhex("94e7a5dcbe816e498b89ab752661904e2f56c485") + bytes(11)
    assert left_aligned == DEV_GOLDEN  # sanity: the developer word IS the canonical layout
    with pytest.raises(ValueError):
        decode_aave_v4_substrate(old_asset)   # reserved bits non-zero
    with pytest.raises(ValueError):
        decode_aave_v4_substrate(old_spoke)   # type 2 is not Reserve
    # unknown flag bits
    bad_flags = int.from_bytes(DEV_GOLDEN, "big") | (0b100 << 48)
    with pytest.raises(ValueError):
        decode_aave_v4_substrate(bad_flags.to_bytes(32, "big"))


def test_canonical_check_reports_only_bad_words_and_ignores_other_encodings():
    old_asset = b"\x01" + bytes(11) + bytes.fromhex("C02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")
    problems = canonical_check("aave_v4_substrate", [DEV_GOLDEN, old_asset])
    assert len(problems) == 1 and old_asset.hex() in problems[0]
    assert canonical_check("address", [old_asset]) == []
