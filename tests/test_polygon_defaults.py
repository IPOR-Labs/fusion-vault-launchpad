"""Polygon context, the FuseWhitelist gate opt-out and resumed batch adoption (offline, SDK-free)."""
from deploy.batch import Simulation, adopt
from deploy.context import CHAIN_IDS, load_context


def test_polygon_context_skips_whitelist_gate():
    ctx = load_context("polygon-fusion")
    assert CHAIN_IDS["polygon-fusion"] == 137
    assert ctx.whitelist_gate_skipped and "empty" in ctx.whitelist_gate_skipped


def test_other_contexts_keep_the_gate():
    for name in ("mainnet-ethereum-fusion", "base-fusion", "arbitrum-fusion", "hyperevm-fusion"):
        assert load_context(name).whitelist_gate_skipped is None


def test_resumed_batch_keeps_hashes_of_steps_done_before():
    sim = Simulation(
        calls=[{"step": "04_balance_fuses"}, {"step": "05_price_feeds"}],
        state={"completed_steps": [
            {"step": "01_clone", "tx_hashes": ["0xlive-clone"]},
            {"step": "04_balance_fuses", "tx_hashes": ["0xfork-a"]},
            {"step": "05_price_feeds", "tx_hashes": ["0xfork-b"]},
        ]},
    )
    out = adopt(sim, ["0xbatch1", "0xbatch1"], keep_steps={"01_clone"})
    hashes = {r["step"]: r["tx_hashes"] for r in out["completed_steps"]}
    assert hashes == {"01_clone": ["0xlive-clone"], "04_balance_fuses": ["0xbatch1"], "05_price_feeds": ["0xbatch1"]}
