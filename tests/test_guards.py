"""deploy.guards — pure pre-broadcast safety checks."""
from deploy.guards import (
    ANVIL_DEFAULT_ACCOUNTS,
    missing_signoffs,
    is_local_node,
    is_public_rpc,
    live_broadcast_problems,
    placeholder_accounts_in_config,
)

ANVIL0 = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
REAL = "0x1111111111111111111111111111111111111111"


def _cfg(owner=REAL, grants=(REAL,), wl=(REAL,), fee=REAL):
    return {
        "vault": {"initial_owner_override": owner},
        "roles": {"grants": [{"role": "OWNER_ROLE", "account": a} for a in grants]},
        "whitelist": {"initial_accounts": list(wl)},
        "fees": {"recipients": [{"address": fee, "split_bps": 10000}]},
        "signoff": {"roles_reviewed": True, "hardening_ack": True, "frontend_listing_ack": True},
    }


def test_anvil_default_list_has_ten_lowercase_entries():
    assert len(ANVIL_DEFAULT_ACCOUNTS) == 10
    assert all(a == a.lower() for a in ANVIL_DEFAULT_ACCOUNTS)


def test_local_node_detection():
    assert is_local_node("anvil/v1.0.0")
    assert is_local_node("HardhatNetwork/2.22.0/@nomicfoundation/ethereumjs-vm/7.0.2")
    assert not is_local_node("Geth/v1.14.0-stable/linux-amd64/go1.22")
    assert not is_local_node("")


def test_public_rpc_detection():
    assert is_public_rpc("https://ethereum-rpc.publicnode.com")
    assert is_public_rpc("https://mainnet.base.org")
    assert not is_public_rpc("https://eth-mainnet.g.alchemy.com/v2/key")
    assert not is_public_rpc("http://127.0.0.1:8546")


def test_clean_config_has_no_test_accounts():
    assert placeholder_accounts_in_config(_cfg()) == []


def test_test_accounts_are_located_by_json_path():
    paths = placeholder_accounts_in_config(_cfg(owner=ANVIL0, grants=(REAL, ANVIL0), wl=(ANVIL0,), fee=REAL))
    assert paths == ["vault.initial_owner_override", "roles.grants[1].account", "whitelist.initial_accounts[0]"]


def test_live_broadcast_refuses_test_accounts_and_signer():
    problems = live_broadcast_problems(_cfg(grants=(ANVIL0,)), signer=ANVIL0, client_version="Geth/v1.14")
    assert len(problems) == 2
    assert "signer" in problems[0]
    assert "roles.grants[0].account" in problems[1]


def test_fork_rehearsal_allows_test_accounts():
    assert live_broadcast_problems(_cfg(grants=(ANVIL0,)), signer=ANVIL0, client_version="anvil/v1.0.0") == []


def test_live_broadcast_clean_config_passes():
    assert live_broadcast_problems(_cfg(), signer=REAL, client_version="Geth/v1.14") == []


# --- sign-off acknowledgements (live chain only) ---
def test_missing_signoffs_lists_each_unacknowledged_item():
    cfg = _cfg(); cfg["signoff"] = {"roles_reviewed": True, "hardening_ack": False}
    m = missing_signoffs(cfg)
    assert len(m) == 2 and "hardening_ack" in m[0] and "frontend_listing_ack" in m[1]
    assert missing_signoffs(_cfg()) == []
    assert len(missing_signoffs({})) == 3


def test_live_broadcast_refuses_without_signoffs_but_fork_does_not():
    cfg = _cfg(); cfg["signoff"] = {}
    live = live_broadcast_problems(cfg, signer=REAL, client_version="Geth/v1.14")
    assert len(live) == 3 and all("signoff." in p for p in live)
    assert live_broadcast_problems(cfg, signer=ANVIL0, client_version="anvil/v1.0.0") == []
