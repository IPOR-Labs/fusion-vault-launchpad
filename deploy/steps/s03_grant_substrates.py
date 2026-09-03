"""Step 03: grant market substrates."""
from __future__ import annotations

from ipor_fusion.core.plasma_vault import PlasmaVault

from deploy.encoders.substrates import encode_substrates

NAME = "03_grant_substrates"


def run(cfg, deploy_ctx, session, instance, state, broadcast):
    if state.has_step(NAME):
        print(f"[{NAME}] already done — skipping")
        return
    vault = PlasmaVault(session.ctx, instance["plasma_vault"])
    tx_hashes = []
    for entry in cfg.raw["substrates"]:
        market_id = deploy_ctx.market_id(entry["market"])
        bytes_list = encode_substrates(entry["encoding"], entry["values"])
        print(f"[{NAME}] market={entry['market']} ({market_id}) "
              f"encoding={entry['encoding']} substrates={len(bytes_list)}")
        rec = session.recorder.add(
            NAME, action="grantMarketSubstrates", key=entry["market"],
            target=instance["plasma_vault"], function="grantMarketSubstrates(uint256,bytes32[])",
            args={"market": entry["market"], "market_id": market_id,
                  "encoding": entry["encoding"], "count": len(bytes_list)},
        )
        if not broadcast:
            continue
        receipt = vault.grant_market_substrates(market_id, bytes_list).send()
        tx_hash = receipt["transactionHash"].hex()
        tx_hashes.append(tx_hash)
        rec.executed = True
        rec.tx_hash = tx_hash
        rec.gas_used = int(receipt["gasUsed"])
        print(f"[{NAME}]   tx {tx_hash} gas={receipt['gasUsed']}")
    if broadcast:
        state.record(NAME, tx_hashes=tx_hashes)
