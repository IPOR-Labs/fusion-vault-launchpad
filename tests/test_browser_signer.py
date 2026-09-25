"""deploy.browser_signer — pure parts: wallet payload, queue hand-off, connection gate."""
import threading
import pytest

from deploy.browser_signer import SigningQueue, sanitize_for_wallet

A = "0x00000000000000000000000000000000000000a1"
B = "0x00000000000000000000000000000000000000b2"
CS = lambda x: x[:2] + x[2:].lower()


def test_sanitize_keeps_only_what_the_wallet_needs_and_hex_encodes():
    tx = {"chainId": 42161, "gas": 210000, "maxFeePerGas": 5, "maxPriorityFeePerGas": 1, "to": B, "from": A, "nonce": 7, "data": b"\x12\x34"}
    out = sanitize_for_wallet(tx)
    assert set(out) == {"from", "to", "data", "value", "gas", "chainId"}
    assert out["data"] == "0x1234" and out["gas"] == hex(210000) and out["chainId"] == hex(42161) and out["value"] == "0x0"
    assert "nonce" not in out and "maxFeePerGas" not in out


def test_connect_gate_checks_chain_then_expected_signer():
    q = SigningQueue(42161, expected_signer=A)
    assert "chain 1" in q.connect(A, 1)
    assert not q.wait_for_connection(0.01)
    assert "not the expected deployer" in q.connect(B, 42161)
    assert q.connect(A, 42161) is None and q.wait_for_connection(0.01)
    assert SigningQueue(42161, None).connect(B, 42161) is None


def test_submit_wait_complete_round_trip_across_threads():
    q = SigningQueue(1, None)
    p = q.submit({"to": B}, "01_clone · clone(...)")
    assert q.state()["pending"]["id"] == p.id and q.state()["pending"]["label"].startswith("01_clone")

    def wallet():
        st = q.state()
        q.complete(st["pending"]["id"], "0xabc")
    threading.Timer(0.05, wallet).start()
    assert q.wait(p, 2.0) == "0xabc"
    assert q.state()["pending"] is None and q.state()["history"][-1]["hash"] == "0xabc"


def test_rejection_and_timeout_raise_and_stale_results_are_ignored():
    q = SigningQueue(1, None)
    p = q.submit({"to": B}, "x")
    assert q.fail(p.id, "user rejected")
    with pytest.raises(RuntimeError, match="user rejected"):
        q.wait(p, 1.0)
    assert not q.complete(p.id, "0x1")          # already settled
    p2 = q.submit({"to": B}, "y")
    with pytest.raises(TimeoutError):
        q.wait(p2, 0.05)
    assert not q.complete(999, "0x1")           # unknown id


def test_only_one_transaction_may_wait_at_a_time():
    q = SigningQueue(1, None)
    q.submit({"to": B}, "first")
    with pytest.raises(RuntimeError, match="already waiting"):
        q.submit({"to": B}, "second")
