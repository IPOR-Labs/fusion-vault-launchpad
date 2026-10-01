"""deploy.role_plan — the base roles and which final grants may be skipped."""
import pytest

from deploy.browser_signer import SigningQueue, SkipRequested
from deploy.role_plan import bootstrap_roles, grant_is_deferred, grant_is_optional

A = "0x00000000000000000000000000000000000000a1"


def test_base_setup_is_atomist_and_fuse_manager_plus_what_configured_steps_need():
    bare = {"price_feeds": [], "instant_withdrawal": {"order": []}, "pre_hooks": []}
    assert [r for r, _ in bootstrap_roles(bare)] == ["ATOMIST_ROLE", "FUSE_MANAGER_ROLE"]
    full = {"price_feeds": [{"asset": A}], "instant_withdrawal": {"order": [{"fuse": "X"}]}, "pre_hooks": [{"x": 1}]}
    assert [r for r, _ in bootstrap_roles(full)] == ["ATOMIST_ROLE", "FUSE_MANAGER_ROLE", "PRICE_ORACLE_MIDDLEWARE_MANAGER_ROLE",
                                                     "CONFIG_INSTANT_WITHDRAWAL_FUSES_ROLE", "PRE_HOOKS_MANAGER_ROLE"]
    assert not {"ALPHA_ROLE", "GUARDIAN_ROLE", "WHITELIST_ROLE", "UPDATE_MARKETS_BALANCES_ROLE"} & {r for r, _ in bootstrap_roles(full)}


def test_only_non_core_grants_are_optional_unless_marked_required():
    assert not grant_is_optional({"role": "OWNER_ROLE", "account": A})
    assert not grant_is_optional({"role": "ATOMIST_ROLE", "account": A})
    assert not grant_is_optional({"role": "FUSE_MANAGER_ROLE", "account": A, "required": False})
    assert grant_is_optional({"role": "ALPHA_ROLE", "account": A})
    assert not grant_is_optional({"role": "GUARDIAN_ROLE", "account": A, "required": True})


def test_the_page_can_skip_an_optional_transaction_but_never_a_required_one():
    q = SigningQueue(1, None)
    p = q.submit({"to": A}, "11_roles · grantRole ALPHA", optional=True)
    assert q.state()["pending"]["optional"] is True
    assert q.skip(p.id)
    with pytest.raises(SkipRequested):
        q.wait(p, 1.0)
    assert q.sends() == {} and q.state()["history"][-1]["error"] == "skipped by the operator"
    r = q.submit({"to": A}, "11_roles · grantRole ATOMIST")
    assert not q.skip(r.id)                       # required: the skip is refused and it keeps waiting
    assert q.state()["pending"]["id"] == r.id


def test_alpha_roles_wait_for_the_alpha():
    for role in ("ALPHA_ROLE", "UPDATE_MARKETS_BALANCES_ROLE", "UPDATE_REWARDS_BALANCE_ROLE", "CLAIM_REWARDS_ROLE", "TRANSFER_REWARDS_ROLE"):
        assert grant_is_deferred({"role": role, "account": A})
    for role in ("ATOMIST_ROLE", "GUARDIAN_ROLE", "WHITELIST_ROLE", "PRE_HOOKS_MANAGER_ROLE"):
        assert not grant_is_deferred({"role": role, "account": A})
