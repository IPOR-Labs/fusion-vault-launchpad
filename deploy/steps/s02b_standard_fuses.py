"""Step 02b: upgrade factory-injected standard fuses to the latest version.

The FusionFactory injects a standard fuse set at clone (e.g. BurnRequestFeeFuse).
That set can lag the whitelist: on 2026-09-01 a vault was cloned with
BurnRequestFeeFuse V1 (0x79e8…) while V2 (0x6Deb…) was already the latest, and
had to be swapped by hand. The deploy context lists ``standard_fuses`` oldest →
newest; this step adds the newest and removes any older ones present.
"""
from __future__ import annotations

from ipor_fusion.core.plasma_vault import PlasmaVault

from deploy.fuses import plan_standard_fuse_upgrade

NAME = "02b_standard_fuses"


def run(cfg, deploy_ctx, session, instance, state, broadcast):
    if state.has_step(NAME):
        print(f"[{NAME}] already done — skipping")
        return
    standard = [str(a) for a in deploy_ctx.standard_fuses()]
    vault = PlasmaVault(session.ctx, instance["plasma_vault"])
    try:
        on_chain = {str(a) for a in vault.get_fuses().call()}
    except Exception as ex:  # dry-run against a not-yet-deployed preview address
        print(f"[{NAME}] vault not deployed yet (dry-run) — standard-fuse upgrade evaluated at broadcast")
        on_chain = set()
    to_add, to_remove = plan_standard_fuse_upgrade(on_chain, standard)
    if not to_add and not to_remove:
        print(f"[{NAME}] standard fuses already at latest ({standard[-1] if standard else 'none'})")
        state.record(NAME)
        return
    print(f"[{NAME}] upgrade standard fuses: add={to_add} remove={to_remove}")
    rec = session.recorder.add(
        NAME, action="upgradeStandardFuses", key=",".join(to_add + to_remove),
        target=instance["plasma_vault"], function="addFuses(address[]) / removeFuses(address[])",
        args={"add": to_add, "remove": to_remove},
    )
    if not broadcast:
        return
    txs = []
    if to_add:
        r = vault.add_fuses([session.ctx.w3.to_checksum_address(a) for a in to_add]).send()
        txs.append(r["transactionHash"].hex()); print(f"[{NAME}] addFuses tx {txs[-1]}")
    if to_remove:
        r = vault.remove_fuses([session.ctx.w3.to_checksum_address(a) for a in to_remove]).send()
        txs.append(r["transactionHash"].hex()); print(f"[{NAME}] removeFuses tx {txs[-1]}")
    rec.executed = True
    rec.tx_hash = txs[-1] if txs else None
    state.record(NAME, tx_hashes=txs)
