"""deploy.config._semantic_checks — cross-field rules the JSON schema can't express."""
import pytest

from deploy.config import _semantic_checks


def _base():
    return {
        "vault": {"underlying": "0xAAA"},
        "price_feeds": [{"asset": "0xAAA"}],
        "fees": {"recipients": []},
        "whitelist": {"enabled_at_launch": True, "initial_accounts": ["0x1"]},
        "transferability": {"enabled_at_launch": False},
    }


def test_valid_config_passes():
    _semantic_checks(_base())  # no raise


def test_fee_split_must_sum_to_10000():
    cfg = _base()
    cfg["fees"]["recipients"] = [{"split_bps": 6000}, {"split_bps": 3000}]
    with pytest.raises(ValueError, match="10000"):
        _semantic_checks(cfg)


def test_fee_split_exactly_10000_ok():
    cfg = _base()
    cfg["fees"]["recipients"] = [{"split_bps": 5000}, {"split_bps": 5000}]
    _semantic_checks(cfg)


def test_whitelist_disabled_with_accounts_raises():
    cfg = _base()
    cfg["whitelist"] = {"enabled_at_launch": False, "initial_accounts": ["0x1"]}
    with pytest.raises(ValueError, match="initial_accounts"):
        _semantic_checks(cfg)


def test_transferability_requires_irreversible_ack():
    cfg = _base()
    cfg["transferability"] = {"enabled_at_launch": True}
    with pytest.raises(ValueError, match="irreversible_ack"):
        _semantic_checks(cfg)


def test_transferability_with_ack_ok():
    cfg = _base()
    cfg["transferability"] = {"enabled_at_launch": True, "irreversible_ack": True}
    _semantic_checks(cfg)


def test_underlying_must_have_price_feed():
    cfg = _base()
    cfg["price_feeds"] = [{"asset": "0xBBB"}]  # underlying 0xAAA has no feed
    with pytest.raises(ValueError, match="price_feeds"):
        _semantic_checks(cfg)


# --- broadcast hygiene: public RPC warning (pure) ---
def test_public_rpc_hosts_are_flagged(capsys):
    from deploy.guards import warn_public_rpc
    warn_public_rpc("https://ethereum-rpc.publicnode.com")
    assert "PUBLIC endpoint" in capsys.readouterr().out
    warn_public_rpc("https://eth-mainnet.g.alchemy.com/v2/key")
    assert capsys.readouterr().out == ""
