"""deploy.roles — role-name resolution (injectable, SDK-free in tests)."""
import pytest

from deploy.roles import resolve_role_id


class _Roles:
    OWNER_ROLE = 1
    ATOMIST_ROLE = 100
    ALPHA_ROLE = 200


def test_resolves_known_role():
    assert resolve_role_id("ATOMIST_ROLE", roles=_Roles) == 100


def test_returns_int_even_if_attr_is_intish():
    class R:
        OWNER_ROLE = "1"  # defensive: coerced to int
    assert resolve_role_id("OWNER_ROLE", roles=R) == 1


def test_unknown_role_raises():
    with pytest.raises(KeyError):
        resolve_role_id("NOT_A_ROLE", roles=_Roles)


def test_default_roles_is_the_real_sdk_registry():
    # Only runs where the SDK is installed; resolves a canonical role to a positive id.
    pytest.importorskip("ipor_fusion")
    assert resolve_role_id("ATOMIST_ROLE") > 0
