"""Rehearsal stage — use the vault on a local fork and prove it works.

Runs after a `--broadcast` against anvil (or on demand with `--rehearse-only`),
never against a live chain: it impersonates a token holder to fund the deployer,
grants the deployer the rehearsal roles it lacks (ALPHA, WHITELIST, balance
updater), deposits, executes every batch the strategy's rehearsal script builds,
checks the accounting after each one, and withdraws through the configured path
(instant, or request → release → withdraw for a scheduled vault).

The decisions live in `deploy/rehearsal_rules.py`; this module only reads the
chain and sends transactions. The report has the same shape as the verification
report and is written to `.deploy-state/<name>.rehearsal.json`.
"""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

from eth_abi import decode as abi_decode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion.core.access import AccessManager
from ipor_fusion import ERC20
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.core.withdraw_manager import WithdrawManager

from deploy.guards import is_local_node
from deploy.rehearsal_rules import (
    RehearsalBatch,
    coverage_problems,
    deposit_problems,
    execute_problems,
    max_drift_bps,
    resolve_deposit_amount,
    withdrawal_mode,
    withdrawal_problems,
)
from deploy.role_helpers import has_role_raw, wait_for_member
from deploy.roles import resolve_role_id

ROOT = Path(__file__).resolve().parent.parent
REHEARSAL_ROLES = ("ALPHA_ROLE", "WHITELIST_ROLE", "UPDATE_MARKETS_BALANCES_ROLE")


class RehearsalError(RuntimeError):
    pass


# --- fork plumbing -------------------------------------------------------------------

def _rpc(w3, method, params):
    res = w3.provider.make_request(method, params)
    if "error" in res:
        raise RehearsalError(f"{method} failed: {res['error']}")
    return res.get("result")


def _impersonated_send(w3, sender: str, to: str, data: bytes) -> dict:
    """Send `data` to `to` from an impersonated `sender` on anvil/hardhat."""
    _rpc(w3, "anvil_impersonateAccount", [sender])
    _rpc(w3, "anvil_setBalance", [sender, hex(10**19)])
    try:
        tx_hash = w3.eth.send_transaction({"from": sender, "to": to, "data": "0x" + data.hex(), "gas": 1_500_000})
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    finally:
        _rpc(w3, "anvil_stopImpersonatingAccount", [sender])
    if receipt["status"] != 1:
        raise RehearsalError(f"impersonated call from {sender} to {to} reverted: {tx_hash.hex()}")
    return receipt


def _advance_time(w3, seconds: int) -> None:
    _rpc(w3, "evm_increaseTime", [int(seconds)])
    _rpc(w3, "evm_mine", [])


def _decimals(session, token: str) -> int:
    raw = session.ctx.call(Web3.to_checksum_address(token), function_signature_to_4byte_selector("decimals()"))
    return int.from_bytes(bytes(raw), "big")


def _load_script(path: str):
    p = ROOT / path
    if not p.exists():
        raise RehearsalError(f"rehearsal.script {path} not found")
    spec = importlib.util.spec_from_file_location(f"rehearsal_{p.stem}", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    if not hasattr(mod, "build_batches"):
        raise RehearsalError(f"{path} must define build_batches(env, stage) -> list[RehearsalBatch]")
    return mod


class RehearsalEnv:
    """What a rehearsal script gets: live wrappers plus the resolved numbers."""

    def __init__(self, cfg, deploy_ctx, session, instance, deposit: int):
        self.cfg, self.deploy_ctx, self.session, self.instance = cfg, deploy_ctx, session, instance
        self.ctx = session.ctx
        self.w3 = session.ctx.web3
        self.vault_address = Web3.to_checksum_address(instance["plasma_vault"])
        self.vault = PlasmaVault(self.ctx, self.vault_address)
        self.underlying = Web3.to_checksum_address(cfg.raw["vault"]["underlying"])
        self.deposit = deposit
        self.deployer = Web3.to_checksum_address(session.signer)

    def fuse(self, name: str):
        return self.deploy_ctx.fuse(name)

    def token(self, name: str):
        return Web3.to_checksum_address(self.deploy_ctx.raw["tokens"][name])

    def erc20(self, address: str) -> ERC20:
        return ERC20(self.ctx, Web3.to_checksum_address(address))

    def balance_of(self, token: str, account: str | None = None) -> int:
        return int(self.erc20(token).balance_of(Web3.to_checksum_address(account or self.vault_address)).call())

    def call(self, to: str, signature: str, args_types: list[str], args: list, out_types: list[str]):
        from eth_abi import encode as abi_encode
        data = function_signature_to_4byte_selector(signature) + abi_encode(args_types, args)
        return abi_decode(out_types, bytes(self.ctx.call(Web3.to_checksum_address(to), data)))

    def block_timestamp(self) -> int:
        return int(self.w3.eth.get_block("latest")["timestamp"])


# --- the stage -------------------------------------------------------------------------

def rehearse(cfg, deploy_ctx, session, instance, state_path: Path) -> dict:
    report: dict = {"ok": [], "warn": [], "fail": [], "observations": {}}

    def _ok(m): report["ok"].append(m); print(f"  ✅ {m}")
    def _warn(m): report["warn"].append(m); print(f"  ⚠️  {m}")
    def _fail(m): report["fail"].append(m); print(f"  ❌ {m}")

    print("\n=== rehearsal: use the vault on the fork ===")
    if not is_local_node(session.client_version):
        raise RehearsalError("rehearsal impersonates accounts and moves time; it runs only against a local fork (anvil/hardhat)")
    r = cfg.raw.get("rehearsal", {})
    w3 = session.ctx.web3
    underlying = Web3.to_checksum_address(cfg.raw["vault"]["underlying"])
    dec = _decimals(session, underlying)
    deposit = resolve_deposit_amount(cfg.raw, dec)
    env = RehearsalEnv(cfg, deploy_ctx, session, instance, deposit)
    vault, deployer = env.vault, env.deployer
    usdc = env.erc20(underlying)
    access = AccessManager(session.ctx, Web3.to_checksum_address(instance["access_manager"]))
    am_address = Web3.to_checksum_address(instance["access_manager"])
    markets = {bf["market"]: deploy_ctx.market_id(bf["market"]) for bf in cfg.raw["balance_fuses"]}

    # 1. roles the deployer needs only for the rehearsal (fork-only; recorded, never on a live chain)
    granted = []
    for role in REHEARSAL_ROLES:
        rid = resolve_role_id(role)
        if not has_role_raw(w3, am_address, rid, deployer):
            access.grant_role(rid, deployer, 0).send()
            wait_for_member(w3, am_address, rid, deployer, timeout_s=10, poll_interval_s=0.2)
            granted.append(role)
    _ok(f"deployer {deployer} holds rehearsal roles (granted now: {granted or 'none'})")

    # 2. fund the deployer from a token holder (impersonated on the fork)
    holder_ref = r.get("token_holder")
    if not holder_ref:
        raise RehearsalError("rehearsal.token_holder is required: an address (or context key) holding the underlying on the fork")
    holder = deploy_ctx.resolve_address(holder_ref)
    if env.balance_of(underlying, holder) < deposit:
        raise RehearsalError(f"token holder {holder} has less than {deposit} of the underlying at this block; pick another")
    _impersonated_send(w3, holder, underlying, usdc.transfer(deployer, deposit).data)
    _ok(f"funded deployer with {deposit / 10**dec:g} underlying from {holder_ref}")

    # 3. deposit
    nav0 = int(vault.total_assets().call())
    shares0 = int(vault.balance_of(deployer).call())
    usdc.approve(env.vault_address, deposit).send()
    vault.deposit(deposit, deployer).send()
    nav1 = int(vault.total_assets().call())
    shares1 = int(vault.balance_of(deployer).call())
    report["observations"]["deposit"] = {"amount": deposit, "nav_before": nav0, "nav_after": nav1, "shares": shares1 - shares0}
    for p in deposit_problems(deposit, nav0, nav1, shares1 - shares0):
        _fail(p)
    if not deposit_problems(deposit, nav0, nav1, shares1 - shares0):
        _ok(f"deposit credited 1:1 (totalAssets {nav0} -> {nav1}, shares minted {shares1 - shares0})")

    # 4. execute the strategy batches from the script, checking the accounting after each
    batches: list[RehearsalBatch] = []
    if r.get("script"):
        mod = _load_script(r["script"])
        batches = list(mod.build_batches(env, "open"))
    else:
        _warn("no rehearsal.script: no fuse is executed; only deposit and withdrawal are rehearsed")
    for p in coverage_problems(cfg.raw, batches):
        _fail(p)
    if batches and not coverage_problems(cfg.raw, batches):
        _ok(f"every declared fuse is exercised by the {len(batches)} batch(es): {sorted({n for b in batches for n in b.exercises})}")

    drift_limit = max_drift_bps(cfg.raw)
    nav_prev = nav1
    for b in batches:
        vault.execute(b.actions).send()
        nav_cached = int(vault.total_assets().call())
        vault.update_markets_balances(list(markets.values())).send()
        nav_fresh = int(vault.total_assets().call())
        values = {name: int(vault.total_assets_in_market(mid).call()) for name, mid in markets.items()}
        idle = env.balance_of(underlying)
        report["observations"][f"execute:{b.label}"] = {"nav_before": nav_prev, "nav_cached": nav_cached, "nav_fresh": nav_fresh,
                                                         "idle_underlying": idle, "markets": values, "exercises": b.exercises}
        probs = execute_problems(b.label, nav_prev, nav_cached, nav_fresh, values, drift_limit)
        for p in probs:
            _fail(p)
        if not probs:
            drift = (nav_cached - nav_prev) * 10_000 / nav_prev if nav_prev else 0
            _ok(f"execute '{b.label}': totalAssets {nav_prev} -> {nav_fresh} ({drift:+.1f} bps), cached == refreshed, "
                f"markets {{{', '.join(f'{k}: {v}' for k, v in values.items())}}}, idle {idle}")
        nav_prev = nav_fresh

    # 5. unwind (optional; a leveraged vault must free the underlying before it can pay a withdrawal)
    if r.get("script") and hasattr(mod, "build_batches"):
        unwind = list(mod.build_batches(env, "unwind"))
        for b in unwind:
            vault.execute(b.actions).send()
            vault.update_markets_balances(list(markets.values())).send()
            nav_fresh = int(vault.total_assets().call())
            probs = execute_problems(b.label, nav_prev, int(vault.total_assets().call()), nav_fresh,
                                     {n: int(vault.total_assets_in_market(m).call()) for n, m in markets.items()}, drift_limit)
            for p in probs:
                _fail(p)
            if not probs:
                _ok(f"execute '{b.label}': totalAssets {nav_prev} -> {nav_fresh}, idle {env.balance_of(underlying)}")
            nav_prev = nav_fresh

    # 6. withdraw through the configured path
    mode = withdrawal_mode(cfg.raw)
    nav_before_w = int(vault.total_assets().call())
    bal_before = env.balance_of(underlying, deployer)
    if mode == "instant":
        want = min(deposit // 2, int(vault.max_withdraw(deployer).call()))
        if want <= 0:
            _fail("nothing is instantly withdrawable: no idle underlying and no instant-withdrawal fuse can free any")
        else:
            vault.withdraw(want, deployer, deployer).send()
    else:
        # Scheduled path, as the SDK walks it (tests/test_simulate_release_funds.py, shares variant):
        # requestShares -> time passes -> ALPHA releaseFunds(timestamp, shares) -> redeemFromRequest.
        wm = WithdrawManager(session.ctx, Web3.to_checksum_address(instance["withdraw_manager"]))
        shares = int(vault.balance_of(deployer).call()) // 2
        wm.request_shares(shares).send()
        _advance_time(w3, 3600)
        wm.release_funds(timestamp=env.block_timestamp() - 1, shares=shares).send()
        _advance_time(w3, 60)
        want = int(vault.convert_to_assets(shares).call())
        vault.redeem_from_request(shares, deployer, deployer).send()
    if want > 0:
        paid = env.balance_of(underlying, deployer) - bal_before
        nav_after_w = int(vault.total_assets().call())
        report["observations"]["withdrawal"] = {"mode": mode, "requested": want, "paid": paid, "nav_before": nav_before_w, "nav_after": nav_after_w}
        probs = withdrawal_problems(want, paid, nav_before_w, nav_after_w)
        for p in probs:
            _fail(p)
        if not probs:
            _ok(f"{mode} withdrawal paid {paid} of {want} requested; totalAssets {nav_before_w} -> {nav_after_w}")

    print(f"\nrehearsal: {len(report['ok'])} ok, {len(report['warn'])} warn, {len(report['fail'])} fail")
    out = state_path.with_name(f"{state_path.stem}.rehearsal.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({**report, "generated_at": int(time.time()), "vault": env.vault_address}, indent=2, default=str))
    print(f"Wrote rehearsal report: {out}")
    return report
