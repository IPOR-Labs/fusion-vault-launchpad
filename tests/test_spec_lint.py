"""tools.spec_lint — ellipsis-aware address matching + reconciliation verdicts."""
import json

import pytest

from tools.spec_lint import _token_matches, _collect_json_addresses, lint

ADDR = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


def test_full_address_match():
    assert _token_matches(ADDR, {ADDR})
    assert not _token_matches(ADDR, {"0x" + "0" * 40})


def test_ellipsis_unicode_match():
    assert _token_matches("0xA0b8…eB48", {ADDR})


def test_ellipsis_ascii_match():
    assert _token_matches("0xA0b8...eB48", {ADDR})


def test_ellipsis_non_match():
    assert not _token_matches("0xDEAD…BEEF", {ADDR})


def test_collect_includes_bytes32_blobs():
    blob = "0x" + "ab" * 32  # 64 hex = bytes32 substrate value
    found = _collect_json_addresses({"substrates": [{"values": [blob]}]})
    assert blob.lower() in found


def _write_pair(tmp_path, cfg, md):
    j = tmp_path / "s.json"
    m = tmp_path / "s.md"
    j.write_text(json.dumps(cfg))
    m.write_text(md)
    return j, m


def test_lint_clean(tmp_path):
    cfg = {"vault": {"name": "My Vault", "symbol": "MV", "underlying": ADDR},
           "chain": {"context": "does-not-exist"}}
    j, m = _write_pair(tmp_path, cfg, f"My Vault (MV) underlying {ADDR}")
    assert lint(j, m) == 0


def test_lint_errors_on_missing_symbol(tmp_path):
    cfg = {"vault": {"name": "My Vault", "symbol": "MV", "underlying": ADDR},
           "chain": {"context": "does-not-exist"}}
    j, m = _write_pair(tmp_path, cfg, f"My Vault underlying {ADDR}")  # no symbol
    assert lint(j, m) == 1  # ERROR


def test_lint_underlying_by_symbol_is_warning_not_error(tmp_path):
    cfg = {"vault": {"name": "My Vault", "symbol": "MV", "underlying": ADDR},
           "chain": {"context": "does-not-exist"}}
    j, m = _write_pair(tmp_path, cfg, "My Vault (MV) holds USDC")  # no address at all
    assert lint(j, m) == 0  # underlying-by-symbol => warning, exit 0
