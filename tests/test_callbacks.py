"""deploy.callbacks — flash-loan callback wiring guard + storage layout (pure, SDK-free)."""
import pytest

from deploy.callbacks import (
    CALLBACK_HANDLER_BASE_SLOT,
    HANDLER_NOT_FOUND_SELECTOR,
    REQUIRED_CALLBACKS,
    callback_problems,
    handler_from_slot_word,
    mapping_key,
    selector,
    signature_problems,
    storage_slot_for,
)

MORPHO = "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"
ENTRY = {"handler": "CallbackHandlerMorpho", "sender": "morpho_blue", "signature": "onMorphoFlashLoan(uint256,bytes)"}


def test_selector_matches_solidity():
    # keccak256("onMorphoFlashLoan(uint256,bytes)")[:4] as Morpho's IMorphoFlashLoanCallback uses it
    assert selector("onMorphoFlashLoan(uint256,bytes)").hex() == "31f57072"
    assert HANDLER_NOT_FOUND_SELECTOR == "0x" + selector("HandlerNotFound()").hex()


def test_selector_rejects_non_canonical_signatures():
    for bad in ("onMorphoFlashLoan(uint256 assets, bytes data)", "onMorphoFlashLoan", "", "0x31f57072"):
        with pytest.raises(ValueError):
            selector(bad)
    assert signature_problems([{"signature": "onMorphoFlashLoan(uint256 a,bytes b)"}])
    assert signature_problems([ENTRY]) == []


def test_flash_loan_fuse_without_handler_is_a_problem():
    p = callback_problems(["MorphoFlashLoanFuse"], [])
    assert len(p) == 1 and "HandlerNotFound" in p[0] and "CallbackHandlerMorpho" in p[0]


def test_flash_loan_fuse_with_handler_is_fine():
    assert callback_problems(["MorphoFlashLoanFuse", "MorphoCollateralFuse"], [ENTRY]) == []


def test_wrong_sender_or_signature_does_not_satisfy_the_requirement():
    assert callback_problems(["MorphoFlashLoanFuse"], [{**ENTRY, "sender": "aave_v3_pool"}])
    assert callback_problems(["MorphoFlashLoanFuse"], [{**ENTRY, "signature": "onMorphoSupply(uint256,bytes)"}])
    assert callback_problems(["MorphoFlashLoanFuse"], [{**ENTRY, "handler": "CallbackHandlerEuler"}])


def test_fuses_without_a_known_requirement_pass_and_extra_entries_are_allowed():
    assert callback_problems(["AaveV3SupplyFuse", "MorphoSupplyFuse"], []) == []
    assert callback_problems(["AaveV3SupplyFuse"], [ENTRY]) == []


def test_required_callbacks_only_lists_verified_pairs():
    # Every row must be a (context key, canonical signature, handler name) triple.
    for fuse, (sender_ref, sig, handler) in REQUIRED_CALLBACKS.items():
        assert fuse.endswith("Fuse")
        assert not sender_ref.startswith("0x")          # a context key, never a hard-coded address
        assert signature_problems([{"signature": sig}]) == []
        assert handler.startswith("CallbackHandler")


# --- storage layout: pinned so the step's idempotency read and the verifier cannot drift ---

def test_base_slot_is_erc7201_style_and_byte_aligned():
    assert CALLBACK_HANDLER_BASE_SLOT & 0xFF == 0
    assert CALLBACK_HANDLER_BASE_SLOT > 0


def test_mapping_key_is_keccak_of_packed_sender_and_selector():
    from eth_utils import keccak
    expected = keccak(bytes.fromhex(MORPHO[2:]) + bytes.fromhex("31f57072"))
    assert mapping_key(MORPHO, "onMorphoFlashLoan(uint256,bytes)") == expected
    with pytest.raises(ValueError):
        mapping_key("0x1234", "onMorphoFlashLoan(uint256,bytes)")


def test_storage_slot_is_deterministic_and_sender_specific():
    a = storage_slot_for(MORPHO, "onMorphoFlashLoan(uint256,bytes)")
    assert a == storage_slot_for(MORPHO.lower(), "onMorphoFlashLoan(uint256,bytes)")
    assert a != storage_slot_for("0x0000000000000000000000000000000000000001", "onMorphoFlashLoan(uint256,bytes)")
    assert a != storage_slot_for(MORPHO, "onMorphoSupply(uint256,bytes)")


def test_handler_from_slot_word():
    zero = handler_from_slot_word(b"\x00" * 32)
    assert zero == "0x" + "00" * 20
    word = b"\x00" * 12 + bytes.fromhex("314E23a66a07644e6c3Fd1a383bc3d9351C884dD")
    assert handler_from_slot_word(word) == "0x314e23a66a07644e6c3fd1a383bc3d9351c884dd"
    assert handler_from_slot_word(b"\x01") == "0x" + "00" * 19 + "01"   # short word is right-aligned
