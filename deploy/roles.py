"""Role-name resolution against the SDK's ``Roles`` registry.

Single source of truth for turning a JSON role name into its numeric id, so the
bootstrap step (`s01b`), the grant step (`s11`) and the verifier all behave
identically. Previously each site reimplemented ``hasattr(Roles, name)`` and
diverged: `s11` raised on an unknown name while `s01b` and `verify` silently
``continue``d. Silently skipping a grant is a determinism bug — the JSON schema
already constrains role names to a known enum, so a name that still slips
through is a typo we must surface, not swallow.
"""
from __future__ import annotations


def resolve_role_id(role_name: str, roles=None) -> int:
    """Return the numeric id for ``role_name``; raise ``KeyError`` if unknown.

    ``roles`` defaults to the SDK's ``ipor_fusion.config.roles.Roles`` and is
    injectable so the resolver can be unit-tested without importing the SDK.
    """
    if roles is None:
        from ipor_fusion.config.roles import Roles
        roles = Roles
    if not hasattr(roles, role_name):
        raise KeyError(
            f"unknown role {role_name!r} — not defined in "
            "ipor_fusion.config.roles.Roles (typo, or SDK out of date)"
        )
    return int(getattr(roles, role_name))
