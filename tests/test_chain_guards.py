"""A run must never configure a vault the connected chain does not have."""
from deploy.chain_guards import price_problem, resume_problems, target_problem
from deploy.state import DeployState, StepRecord

VAULT, AM = "0x" + "11" * 20, "0x" + "22" * 20
TX = "0x" + "ab" * 32
INSTANCE = {"plasma_vault": VAULT, "access_manager": AM}


def _state(clone_recorded=True, tx=TX):
    steps = [StepRecord(step="01_clone", tx_hashes=[tx] if tx else [])] if clone_recorded else []
    return DeployState(config_hash="x", completed_steps=steps, fusion_instance=dict(INSTANCE))


def code_everywhere(_):
    return b"\x60\x80"


def test_fresh_state_is_fine():
    assert resume_problems(DeployState(config_hash="x"), lambda h: None, lambda a: b"") == []


def test_mined_clone_with_code_resumes():
    assert resume_problems(_state(), lambda h: {"status": 1}, code_everywhere) == []


def test_state_from_another_fork_is_refused():
    # same chain id, but the clone tx is unknown here: an old anvil fork's state used against a new fork or live
    problems = resume_problems(_state(), lambda h: None, lambda a: b"")
    assert any("not on this chain" in p for p in problems)
    assert any("plasma_vault" in p for p in problems) and any("access_manager" in p for p in problems)


def test_reverted_clone_is_refused():
    assert any("reverted" in p for p in resume_problems(_state(), lambda h: {"status": 0}, code_everywhere))


def test_vault_without_code_is_refused_even_with_a_receipt():
    problems = resume_problems(_state(), lambda h: {"status": 1}, lambda a: b"" if a == AM else b"\x01")
    assert problems == [f"access_manager {AM} has no code on this chain"]


def test_clone_recorded_without_hash_is_refused():
    assert any("without a transaction hash" in p for p in resume_problems(_state(tx=None), lambda h: {"status": 1}, code_everywhere))


def test_unsent_preview_is_left_to_the_clone_step():
    # preview addresses without a recorded clone: 01_clone discards them and clones again
    assert resume_problems(_state(clone_recorded=False), lambda h: None, lambda a: b"") == []


def test_send_to_an_address_without_code_is_refused():
    assert "no contract there" in target_problem(AM, b"")
    assert target_problem(None, b"") is not None
    assert target_problem(AM, b"\x60\x80") is None


def test_unprefixed_clone_hash_is_looked_up_with_0x():
    asked = []
    state = _state(tx="ab" * 32)
    assert resume_problems(state, lambda h: asked.append(h) or {"status": 1}, code_everywhere) == []
    assert asked == ["0x" + "ab" * 32]


def test_one_wei_usd_feed_is_refused():
    # the HyperEVM feed 0xb001…0904 answers 1 with 8 decimals: USDC at 1e-8 USD
    assert "outside" in price_problem("feed", 1, 8)


def test_plausible_prices_pass():
    assert price_problem("usdc", 10**8, 8) is None                 # 1 USD
    assert price_problem("stock", 229_31 * 10**16, 18) is None      # 229.31 USD
    assert price_problem("btc", 110_000 * 10**8, 8) is None


def test_zero_and_opt_out():
    assert "no usable price" in price_problem("feed", 0, 8)
    assert price_problem("meme", 1, 8, allow_outside=True) is None


def test_permission_audit_flags_leftovers_only():
    from deploy.chain_guards import permission_problems, role_holders
    me, guardian, vault, safe, parent = ("0x" + c * 40 for c in "12345")
    held = role_holders([("granted", 1, safe), ("granted", 100, safe), ("granted", 1, me), ("revoked", 1, safe),
                         ("granted", 2, guardian), ("granted", 3, vault), ("granted", 4, "0x" + "6" * 40),
                         ("granted", 800, parent)])
    fails, notes = permission_problems(held, {me, guardian}, {vault})
    assert fails == [f"{safe} holds ATOMIST (100) but the spec does not grant it"]   # OWNER was revoked, ATOMIST left behind
    assert notes == [f"{parent} may deposit (WHITELIST)"]
