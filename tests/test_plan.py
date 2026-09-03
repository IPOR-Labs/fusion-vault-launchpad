"""deploy.plan — Action identity + plan↔run diff semantics."""
from deploy.plan import Action, RunRecorder, diff_plan_run


def _recorder(mode="dry-run"):
    return RunRecorder(strategy="t", config_hash="abc", mode=mode, chain_id=1,
                       rpc_source="test", signer="0x0", gas_multiplier=1.0, context="ctx")


def test_identity_ignores_result_fields():
    a = Action(step="02", action="addFuses", key="k", args={"x": 1},
               executed=False, tx_hash=None, target=None)
    b = Action(step="02", action="addFuses", key="k", args={"x": 1},
               executed=True, tx_hash="0xdead", target="0xabc", gas_used=42)
    assert a.identity() == b.identity()


def test_identity_distinguishes_args():
    a = Action(step="02", action="addFuses", key="k", args={"x": 1})
    b = Action(step="02", action="addFuses", key="k", args={"x": 2})
    assert a.identity() != b.identity()


def test_recorder_manifest_fields():
    r = _recorder("broadcast")
    r.add("01", "clone", key="V")
    d = r.to_dict()
    assert d["manifest"]["mode"] == "broadcast"
    assert d["manifest"]["config_hash"] == "abc"
    assert len(d["actions"]) == 1
    assert d["actions"][0]["step"] == "01"


def _plan_action(step, action, key, **args):
    return {"step": step, "action": action, "key": key, "args": args}


def test_diff_clean_match():
    plan = {"actions": [_plan_action("02", "addFuses", "k", n=3)]}
    run = {"actions": [_plan_action("02", "addFuses", "k", n=3)]}
    d = diff_plan_run(plan, run)
    assert len(d["matched"]) == 1 and not d["run_only"] and not d["plan_only"]


def test_diff_flags_unplanned_run_action():
    plan = {"actions": [_plan_action("02", "addFuses", "k")]}
    run = {"actions": [_plan_action("02", "addFuses", "k"),
                       _plan_action("99", "selfDestruct", "x")]}
    d = diff_plan_run(plan, run)
    assert len(d["run_only"]) == 1
    assert d["run_only"][0]["action"] == "selfDestruct"


def test_diff_plan_only_is_benign_skip():
    # Broadcast skipped an already-satisfied action present in the plan.
    plan = {"actions": [_plan_action("05", "registerPriceFeed", "asset1"),
                        _plan_action("05", "registerPriceFeed", "asset2")]}
    run = {"actions": [_plan_action("05", "registerPriceFeed", "asset1")]}
    d = diff_plan_run(plan, run)
    assert not d["run_only"]
    assert len(d["plan_only"]) == 1
    assert d["plan_only"][0]["key"] == "asset2"
