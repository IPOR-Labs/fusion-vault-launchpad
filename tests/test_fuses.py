"""deploy.fuses.classify_fuses — fuse set classification (pure, no chain/SDK)."""
from deploy.fuses import classify_fuses

DECLARED = "0x" + "11" * 20
STANDARD = "0x" + "22" * 20  # e.g. BurnRequestFeeFuse
ROGUE = "0x" + "33" * 20


def test_standard_injected_fuse_is_not_unexpected():
    # on-chain = declared + the factory-injected standard fuse
    missing, unexpected, injected = classify_fuses(
        on_chain={DECLARED, STANDARD}, declared={DECLARED}, standard={STANDARD}
    )
    assert not missing
    assert not unexpected            # BurnRequestFeeFuse must NOT warn
    assert injected == {STANDARD}


def test_missing_declared_fuse_flagged():
    missing, unexpected, injected = classify_fuses(
        on_chain={STANDARD}, declared={DECLARED}, standard={STANDARD}
    )
    assert missing == {DECLARED}
    assert not unexpected


def test_truly_unexpected_fuse_still_warns():
    missing, unexpected, injected = classify_fuses(
        on_chain={DECLARED, STANDARD, ROGUE}, declared={DECLARED}, standard={STANDARD}
    )
    assert not missing
    assert unexpected == {ROGUE}     # a real rogue fuse still surfaces
    assert injected == {STANDARD}


def test_case_insensitive():
    missing, unexpected, injected = classify_fuses(
        on_chain={DECLARED.upper()}, declared={DECLARED.lower()}, standard=set()
    )
    assert not missing and not unexpected


def test_no_standard_configured_behaves_like_before():
    # context without standard_fuses → injected empty, extras warn as unexpected
    missing, unexpected, injected = classify_fuses(
        on_chain={DECLARED, STANDARD}, declared={DECLARED}, standard=set()
    )
    assert unexpected == {STANDARD}
    assert not injected


# --- standard-fuse upgrade plan (BurnRequestFeeFuse V1 -> V2 lesson, 2026-09-01) ---
from deploy.fuses import plan_standard_fuse_upgrade

V1 = "0x79e8B115Bd41baee318c1940F42F1a2d94D29ab4"
V2 = "0x6DebD98329d826bA79b6Fd9B14cC718D1720D0bE"


def test_upgrade_adds_latest_and_removes_older_injected():
    add, rem = plan_standard_fuse_upgrade({V1.lower(), "0xaaaa"}, [V1, V2])
    assert add == [V2.lower()] and rem == [V1.lower()]


def test_upgrade_noop_when_latest_present():
    assert plan_standard_fuse_upgrade({V2}, [V1, V2]) == ([], [])
    assert plan_standard_fuse_upgrade({V2.lower()}, [V1, V2]) == ([], [])


def test_upgrade_handles_empty_standard_list():
    assert plan_standard_fuse_upgrade({V1}, []) == ([], [])


# --- instant-withdraw queue param lengths (AaveV4SupplyFuseInvalidParams lesson, 2026-09-01) ---
from deploy.fuses import queue_param_problems


def test_queue_params_flag_short_aave_v4_entry():
    order = [{"fuse": "SupplyFuseAaveV4", "params": [{}, {}, {}, {}]},
             {"fuse": "AaveV3SupplyFuse", "params": [{}, {}]},
             {"fuse": "SupplyFuseEulerV2", "params": [{}, {}]}]
    probs = queue_param_problems(order)
    assert any("SupplyFuseAaveV4: 4 params" in p and ">= 5" in p for p in probs)
    assert any("SupplyFuseEulerV2: 2 params" in p for p in probs)
    assert not any("AaveV3SupplyFuse" in p for p in probs)


def test_queue_params_ok_and_unknown_family_passes():
    assert queue_param_problems([{"fuse": "SupplyFuseAaveV4", "params": [{}] * 5}, {"fuse": "ExoticFuse", "params": []}]) == []
