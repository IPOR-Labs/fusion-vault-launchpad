"""Step 06: WithdrawManager.update_withdraw_window."""
from __future__ import annotations

from ipor_fusion.core.withdraw_manager import WithdrawManager

NAME = "06_withdraw_manager"


def run(cfg, deploy_ctx, session, instance, state, broadcast):
    if state.has_step(NAME):
        print(f"[{NAME}] already done — skipping")
        return
    wm = WithdrawManager(session.ctx, instance["withdraw_manager"])
    window = int(cfg.raw["withdraw_manager"]["window_seconds"])
    if window <= 0:
        # Instant-only vault: no scheduled withdraw window. The WithdrawManager
        # rejects a zero window (WithdrawWindowLengthCannotBeZero), and instant
        # redemptions bypass the window anyway — so leave it unset.
        print(f"[{NAME}] window_seconds=0 (instant-only) — no withdraw window to set, skipping")
        state.record(NAME)
        return
    print(f"[{NAME}] withdraw_window={window}s ({window/3600:.1f}h)")
    rec = session.recorder.add(
        NAME, action="updateWithdrawWindow", key="window",
        target=instance["withdraw_manager"], function="updateWithdrawWindow(uint256)",
        args={"window_seconds": window},
    )
    if not broadcast:
        return
    receipt = wm.update_withdraw_window(window).send()
    tx_hash = receipt["transactionHash"].hex()
    print(f"[{NAME}] tx {tx_hash} gas={receipt['gasUsed']}")
    rec.executed = True
    rec.tx_hash = tx_hash
    rec.gas_used = int(receipt["gasUsed"])
    state.record(NAME, tx_hashes=[tx_hash])
