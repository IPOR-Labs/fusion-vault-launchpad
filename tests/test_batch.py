"""Batch mode: split by block gas, and hand the simulated state to the live run with live hashes."""
import pytest

from deploy.batch import Simulation, access_manager_txs, adopt, batch_gas_limit, chunks


def _c(gas, step="s"):
    return {"to": "0x" + "11" * 20, "data": "0x", "value": "0x0", "gas_used": gas, "step": step, "label": step}


def test_whole_vault_fits_one_arbitrum_block():
    calls = [_c(9_000_000, "01_clone")] + [_c(100_000) for _ in range(25)]
    assert len(chunks(calls, 32_000_000)) == 1


def test_split_keeps_order_and_block_headroom():
    calls = [_c(9_000_000, "a"), _c(9_000_000, "b"), _c(9_000_000, "c")]
    parts = chunks(calls, 30_000_000)                   # 80 % of 30 M = 24 M per batch
    assert [[c["step"] for c in p] for p in parts] == [["a", "b"], ["c"]]


def test_call_too_big_for_a_block_is_refused():
    with pytest.raises(RuntimeError):
        chunks([_c(40_000_000)], 32_000_000)


def test_hyperevm_uses_the_big_block_limit():
    assert batch_gas_limit(999, 3_000_000) == 30_000_000
    assert batch_gas_limit(42161, 1_125_899_906_842_624) == 30_000_000   # Arbitrum's reported block limit
    assert batch_gas_limit(1, 45_000_000) == 30_000_000


def test_adopt_replaces_fork_hashes_with_live_ones():
    sim = Simulation(calls=[_c(1, "01_clone"), _c(1, "02_add_fuses"), _c(1, "02_add_fuses")],
                     state={"config_hash": "x", "fusion_instance": {"plasma_vault": "0x1"},
                            "completed_steps": [{"step": "01_clone", "tx_hashes": ["0xfork1"], "notes": {}},
                                                {"step": "02_add_fuses", "tx_hashes": ["0xfork2"], "notes": {}},
                                                {"step": "12_transferability", "tx_hashes": [], "notes": {}}]})
    live = adopt(sim, ["0xlive", "0xlive", "0xlive"])
    assert [r["tx_hashes"] for r in live["completed_steps"]] == [["0xlive"], ["0xlive"], []]


AM, VAULT, FACTORY, CHILD_AM = "0x" + "aa" * 20, "0x" + "bb" * 20, "0x" + "cc" * 20, "0x" + "dd" * 20


def _to(to, step, gas=100_000):
    return {"to": to, "data": "0x12345678", "value": "0x0", "gas_used": gas, "step": step, "label": step}


def test_vault_calls_pack_into_access_manager_multicalls():
    calls = [_to(FACTORY, "01_clone", 9_000_000), _to(AM, "01b_bootstrap_roles"), _to(VAULT, "02_add_fuses"),
             _to(VAULT, "03_grant_substrates"), _to(FACTORY, "05_feed_create"), _to(VAULT, "05_price_feeds"),
             _to(CHILD_AM, "12b_link_whitelist")]
    txs = access_manager_txs(calls, AM, {VAULT}, 30_000_000)
    assert [t["label"] for t in txs] == ["01_clone", "AccessManager.multicall · 3 calls", "05_feed_create",
                                         "AccessManager.multicall · 1 calls", "12b_link_whitelist"]
    mc = txs[1]
    assert mc["to"] == AM and mc["data"].startswith("0xac9650d8")          # multicall(bytes[])
    assert "1cff79cd" in mc["data"]                                          # execute(address,bytes) for vault calls
    assert sum(len(t["calls"]) for t in txs) == len(calls)
