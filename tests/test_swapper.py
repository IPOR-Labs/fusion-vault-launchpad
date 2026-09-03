"""deploy.swapper — fuse family vs substrate encoding guard (pure, SDK-free)."""
import pytest

from deploy.swapper import swapper_problems, FUSE_FAMILY

TOK = {"kind": "token", "address": "0x0000000000000000000000000000000000000001"}
TGT_SEL = {"kind": "target", "target": "0x0000000000000000000000000000000000000002", "selector": "0xaabbccdd"}
TGT_TYPED = {"kind": "target", "address": "0x0000000000000000000000000000000000000002"}


def _sub(market, encoding, values):
    return [{"market": market, "encoding": encoding, "values": values}]


def test_with_verification_accepts_selector_encoding_with_tokens_and_targets():
    assert swapper_problems(["UniversalTokenSwapperWithVerificationFuse"],
                            _sub("UNIVERSAL_TOKEN_SWAPPER", "universal_selector_substrate", [TOK, TGT_SEL])) == []


def test_typed_fuse_accepts_typed_encoding_and_v2_is_market_1202():
    assert swapper_problems(["UniversalTokenSwapperFuse"],
                            _sub("UNIVERSAL_TOKEN_SWAPPER", "universal_typed_substrate", [TOK, TGT_TYPED])) == []
    assert swapper_problems(["UniversalTokenSwapperFuseV2"],
                            _sub("UNIVERSAL_TOKEN_SWAPPER_V2", "universal_typed_substrate", [TOK, TGT_TYPED])) == []
    # the same substrates on market 12 have no fuse of that family there
    assert swapper_problems(["UniversalTokenSwapperFuseV2"],
                            _sub("UNIVERSAL_TOKEN_SWAPPER", "universal_typed_substrate", [TOK, TGT_TYPED]))


def test_legacy_fuse_takes_plain_addresses():
    assert swapper_problems(["SwapFuseUniversalTokenSwapper"],
                            _sub("UNIVERSAL_TOKEN_SWAPPER", "address", ["0x0000000000000000000000000000000000000001"])) == []


def test_wrong_family_encoding_is_rejected():
    p = swapper_problems(["UniversalTokenSwapperWithVerificationFuse"],
                         _sub("UNIVERSAL_TOKEN_SWAPPER", "universal_typed_substrate", [TOK, TGT_TYPED]))
    assert p and "does not match" in p[0]
    p = swapper_problems(["UniversalTokenSwapperFuse"],
                         _sub("UNIVERSAL_TOKEN_SWAPPER", "address", ["0x0000000000000000000000000000000000000001"]))
    assert p and "does not match" in p[0]


def test_missing_token_or_target_words_are_flagged():
    p = swapper_problems(["UniversalTokenSwapperWithVerificationFuse"],
                         _sub("UNIVERSAL_TOKEN_SWAPPER", "universal_selector_substrate", [TGT_SEL]))
    assert any("no token word" in x for x in p)
    p = swapper_problems(["UniversalTokenSwapperFuse"],
                         _sub("UNIVERSAL_TOKEN_SWAPPER", "universal_typed_substrate", [TOK]))
    assert any("no target word" in x for x in p)


def test_mixed_families_on_one_market_and_unknown_variant_are_flagged():
    p = swapper_problems(["UniversalTokenSwapperFuse", "UniversalTokenSwapperWithVerificationFuse"], [])
    assert any("mixed families" in x for x in p)
    p = swapper_problems(["UniversalTokenSwapperTurboFuse"], [])
    assert any("unknown family" in x for x in p)
    assert swapper_problems(["UniversalTokenSwapperBalanceFuse"], []) == []   # balance fuse is not a family


def test_substrates_without_a_swapper_fuse_are_flagged_and_other_markets_ignored():
    assert swapper_problems([], _sub("UNIVERSAL_TOKEN_SWAPPER", "universal_selector_substrate", [TOK, TGT_SEL]))
    assert swapper_problems([], _sub("AAVE_V3", "address", ["0x0000000000000000000000000000000000000001"])) == []


def test_every_family_entry_maps_to_a_known_encoding():
    assert {enc for _, enc in FUSE_FAMILY.values()} == {"address", "universal_typed_substrate", "universal_selector_substrate"}
