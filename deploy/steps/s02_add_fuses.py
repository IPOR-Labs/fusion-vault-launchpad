"""Step 02: add fuses to PlasmaVault."""
from __future__ import annotations

from ipor_fusion.core.plasma_vault import PlasmaVault

from deploy.whitelist import assert_whitelisted

NAME = "02_add_fuses"


def run(cfg, deploy_ctx, session, instance, state, broadcast):
    if state.has_step(NAME):
        print(f"[{NAME}] already done — skipping")
        return
    # FuseWhitelist gate (runs in dry-run too): every fuse the config references —
    # functional, balance, instant-withdraw — must be listed and `active` on the
    # chain's FuseWhitelist. The context map is only a cache of that registry and
    # has drifted before (typed swapper on market 12 is `removed`, Base
    # "AaveV3CollateralFuse" was a deprecated supply fuse — 2026-09-03).
    print(f"[{NAME}] FuseWhitelist gate @ {deploy_ctx.fuse_whitelist}")
    assert_whitelisted(cfg.raw, deploy_ctx, session.ctx.web3)
    vault = PlasmaVault(session.ctx, instance["plasma_vault"])
    addrs = [deploy_ctx.fuse(f["name"]) for f in cfg.raw["fuses"]]
    print(f"[{NAME}] adding {len(addrs)} fuses:")
    for f, a in zip(cfg.raw["fuses"], addrs):
        print(f"           {f['name']} -> {a}")
    rec = session.recorder.add(
        NAME, action="addFuses", key=",".join(f["name"] for f in cfg.raw["fuses"]),
        target=instance["plasma_vault"], function="addFuses(address[])",
        args={"fuses": [f["name"] for f in cfg.raw["fuses"]],
              "addresses": [str(a) for a in addrs]},
    )
    if not broadcast:
        return
    receipt = vault.add_fuses(addrs).send()
    tx_hash = receipt["transactionHash"].hex()
    print(f"[{NAME}] tx {tx_hash} gas_used={receipt['gasUsed']}")
    rec.executed = True
    rec.tx_hash = tx_hash
    rec.gas_used = int(receipt["gasUsed"])
    state.record(NAME, tx_hashes=[tx_hash])
