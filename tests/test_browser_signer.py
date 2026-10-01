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


# --- progress board: plan vs run, per step -------------------------------------------

import json as _json
import urllib.request
import urllib.error

from deploy.browser_signer import BrowserSigner, build_board, load_plan

STEPS = ["01_clone", "01b_bootstrap_roles", "07_fees", "11_roles"]
PLAN = [
    {"step": "01_clone", "action": "clone", "key": "V", "args": {"name": "V"}},
    {"step": "01b_bootstrap_roles", "action": "grantRole", "key": "OWNER_ROLE", "args": {"role_id": 1}},
    {"step": "01b_bootstrap_roles", "action": "grantRole", "key": "ATOMIST_ROLE", "args": {"role_id": 100}},
    {"step": "11_roles", "action": "grantRole", "key": "ALPHA_ROLE", "args": {"to": A}},
]


def _board(run, sends=None, current=None, finished=(), failed=None, earlier=None):
    return build_board(STEPS, PLAN, run, sends or {}, current=current, failed=failed,
                       finished_steps=set(finished), earlier_steps=earlier or {})


def test_board_before_the_first_transaction_lists_every_planned_signature():
    b = _board([])
    assert b["planKnown"] and b["total"] == 4 and b["signed"] == 0
    assert [s["status"] for s in b["steps"]] == ["upcoming"] * 4
    assert [len(s["actions"]) for s in b["steps"]] == [1, 2, 0, 1]


def test_board_tracks_signed_waiting_and_current_step():
    run = [
        {"step": "01_clone", "action": "clone", "key": "V", "args": {"name": "V"}, "executed": True, "tx_hash": "0x01", "gas_used": 9},
        {"step": "01b_bootstrap_roles", "action": "grantRole", "key": "OWNER_ROLE", "args": {"role_id": 1}},
    ]
    b = _board(run, sends={1: {"hash": None, "error": None, "pending": True}}, current="01b_bootstrap_roles", finished={"01_clone"})
    clone, boot = b["steps"][0], b["steps"][1]
    assert clone["status"] == "done" and clone["actions"][0]["status"] == "signed" and clone["actions"][0]["tx_hash"] == "0x01"
    assert boot["status"] == "current" and [a["status"] for a in boot["actions"]] == ["waiting", "upcoming"]
    assert b["signed"] == 1 and b["total"] == 4


def test_board_matches_loosely_when_only_the_args_differ_and_flags_unplanned_actions():
    run = [
        {"step": "11_roles", "action": "grantRole", "key": "ALPHA_ROLE", "args": {"to": B}, "executed": True, "tx_hash": "0x02"},
        {"step": "11_roles", "action": "renounceRole", "key": "OWNER_ROLE", "args": {}, "executed": True, "tx_hash": "0x03"},
    ]
    roles = _board(run, finished={"11_roles"})["steps"][3]
    assert [(a["status"], a["planned"]) for a in roles["actions"]] == [("signed", True), ("signed", False)]


def test_board_marks_failed_and_earlier_steps_and_unused_plan_rows():
    b = _board([{"step": "07_fees", "action": "x", "key": "", "args": {}}],
               sends={0: {"hash": None, "error": "user rejected", "pending": False}},
               failed={"step": "07_fees", "error": "wallet did not sign"},
               earlier={"01_clone": ["0xaa"], "01b_bootstrap_roles": ["0xbb", "0xcc"]}, finished={"11_roles"})
    s = {x["name"]: x for x in b["steps"]}
    assert s["01_clone"]["status"] == "earlier" and s["01_clone"]["earlier_hashes"] == ["0xaa"]
    assert s["07_fees"]["status"] == "failed" and s["07_fees"]["actions"][0]["status"] == "rejected"
    assert s["11_roles"]["actions"][0]["status"] == "not-needed"
    assert b["signed"] == 3            # three earlier-run signatures count as done


def test_load_plan_refuses_a_stale_or_missing_artifact(tmp_path):
    p = tmp_path / "v.plan.json"
    assert load_plan(p, "abc")[0] is None
    p.write_text(_json.dumps({"manifest": {"config_hash": "old"}, "actions": PLAN}))
    planned, why = load_plan(p, "abc")
    assert planned is None and "different version" in why
    p.write_text(_json.dumps({"manifest": {"config_hash": "abc"}, "actions": PLAN + [{"step": "07_fees", "action": "s", "skipped": True}]}))
    assert load_plan(p, "abc") == (PLAN, None)


def test_server_serves_the_token_inline_and_refuses_posts_without_it(tmp_path):
    page = tmp_path / "index.html"
    page.write_text("<script>const TOKEN='__SIGNER_TOKEN__'</script>")
    s = BrowserSigner(SigningQueue(1, None), port=0, page=page, log=lambda *_: None, meta={"steps": STEPS, "planned": PLAN})
    s.start()
    port = s._server.server_address[1]
    try:
        base = f"http://127.0.0.1:{port}"
        html = urllib.request.urlopen(base + "/").read().decode()
        assert s.token in html and "__SIGNER_TOKEN__" not in html
        st = _json.loads(urllib.request.urlopen(base + "/api/state").read())
        assert st["board"]["total"] == 4

        def post(headers, extra={}):
            req = urllib.request.Request(base + "/api/connect", data=_json.dumps({"account": A, "chainId": 1, **extra}).encode(),
                                         headers={"Content-Type": "application/json", **headers})
            try:
                return urllib.request.urlopen(req).status
            except urllib.error.HTTPError as e:
                return e.code
        assert post({}) == 403
        assert post({"X-Signer-Token": "wrong"}) == 403
        assert post({"X-Signer-Token": s.token, "Host": f"evil.example:{port}"}) == 403
        assert post({"X-Signer-Token": s.token}) == 200 and s.queue.account is None   # disclaimer not accepted
        assert s.disclaimer_accepted_at is None
        assert post({"X-Signer-Token": s.token}, {"accepted": True}) == 200 and s.queue.account and s.disclaimer_accepted_at
    finally:
        s.stop()


def test_a_step_run_again_is_current_not_earlier():
    run = [{"step": "11_roles", "action": "grantRole", "key": "ALPHA_ROLE", "args": {"to": A}}]
    b = _board(run, sends={0: {"hash": None, "error": None, "pending": True}}, current="11_roles",
               earlier={"01_clone": ["0xaa"], "11_roles": ["0xbb"]})
    s = {x["name"]: x for x in b["steps"]}
    assert s["11_roles"]["status"] == "current" and s["01_clone"]["status"] == "earlier"
    b = _board([], finished={"01_clone"}, earlier={"01_clone": ["0xaa"]})   # skipped because done earlier
    assert b["steps"][0]["status"] == "earlier"


def test_fork_run_refuses_a_wallet_on_the_live_chain():
    # a fork has the live chain id; only the marker balance, read through the wallet, tells them apart
    from deploy.browser_signer import SigningQueue
    acct = "0x1111111111111111111111111111111111111111"
    q = SigningQueue(42161, acct)
    q.fork_marker = {"address": "0x" + "33" * 20, "balance": hex(123456789)}
    assert "LIVE chain" in q.connect(acct, 42161, "0x0")          # wallet on the live RPC sees nothing
    assert "LIVE chain" in q.connect(acct, 42161, None)           # page could not read it
    assert not q.wait_for_connection(0)
    assert q.connect(acct, 42161, hex(123456789)) is None         # wallet on the fork
    assert q.wait_for_connection(0)


def test_live_run_needs_no_marker():
    from deploy.browser_signer import SigningQueue
    acct = "0x1111111111111111111111111111111111111111"
    assert SigningQueue(42161, acct).connect(acct, 42161) is None
