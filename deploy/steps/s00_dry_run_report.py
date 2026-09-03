"""Print a complete plan: which steps, which calls."""
from __future__ import annotations

NAME = "00_dry_run_report"


def run(cfg, deploy_ctx, session, instance, state, broadcast):
    print("=" * 72)
    print(f"PlasmaVault deploy — strategy: {cfg.name}")
    print(f"  chain_id={cfg.chain_id} context={cfg.context_name}")
    print(f"  signer={session.signer} broadcast={broadcast}")
    print(f"  vault: {cfg.raw['vault']['name']} ({cfg.raw['vault']['symbol']})")
    print(f"  underlying={cfg.raw['vault']['underlying']} cap={cfg.raw['vault'].get('total_supply_cap')}")
    print(f"  redemption_delay={cfg.raw['vault']['redemption_delay_seconds']}s")
    print(f"  fuses={len(cfg.raw['fuses'])}, balance_fuses={len(cfg.raw['balance_fuses'])}")
    print(f"  substrates={len(cfg.raw['substrates'])}, price_feeds={len(cfg.raw['price_feeds'])}")
    print(f"  role grants={len(cfg.raw['roles']['grants'])}")
    print(f"  whitelist_enabled={cfg.raw['whitelist']['enabled_at_launch']}")
    print(f"  transferable_at_launch={cfg.raw['transferability']['enabled_at_launch']}")
    print("=" * 72)
