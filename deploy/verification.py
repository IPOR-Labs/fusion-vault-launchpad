"""Post-deploy sanity checks: read chain state, compare against config."""
from __future__ import annotations

from decimal import Decimal

from eth_abi import decode as abi_decode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion.core.access import AccessManager
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.core.withdraw_manager import WithdrawManager

from deploy.encoders.feeds import build_get_source_of_asset_price, build_get_asset_price
from deploy.callbacks import handler_from_slot_word, storage_slot_for
from deploy.encoders.substrates import encode_substrates, canonical_check
from deploy.fuses import queue_param_problems
from deploy.fuses import classify_fuses
from deploy.whitelist import read_fuse_statuses, whitelist_problems
from deploy.graph import derive_dependency_graph
from deploy.roles import resolve_role_id


def verify(cfg, deploy_ctx, session, instance) -> dict:
    """Returns a dict report. Prints findings. Doesn't raise — caller decides."""
    report: dict = {"ok": [], "warn": [], "fail": []}

    vault_addr = instance["plasma_vault"]
    vault = PlasmaVault(session.ctx, vault_addr)
    access = AccessManager(session.ctx, instance["access_manager"])
    wm = WithdrawManager(session.ctx, instance["withdraw_manager"])

    def _ok(msg): report["ok"].append(msg); print(f"  ✅ {msg}")
    def _warn(msg): report["warn"].append(msg); print(f"  ⚠️  {msg}")
    def _fail(msg): report["fail"].append(msg); print(f"  ❌ {msg}")

    print("\n=== verification report ===")

    # 1. underlying asset
    try:
        underlying = vault.underlying_asset_address().call()
        if underlying.lower() == cfg.raw["vault"]["underlying"].lower():
            _ok(f"underlying asset matches: {underlying}")
        else:
            _fail(f"underlying mismatch: vault={underlying} cfg={cfg.raw['vault']['underlying']}")
    except Exception as e:
        _fail(f"underlying read failed: {e}")

    # 2. fuses — declared (strategy JSON) vs on-chain, accounting for the standard
    # factory-injected fuses (e.g. BurnRequestFeeFuse) that every vault carries.
    # A declared fuse missing on-chain is a ❌; a fuse that is neither declared nor
    # standard is a ⚠️ (genuinely unexpected — surface it loudly).
    try:
        on_chain = {a.lower() for a in vault.get_fuses().call()}
        declared = {deploy_ctx.fuse(f["name"]).lower() for f in cfg.raw["fuses"]}
        standard = {a.lower() for a in deploy_ctx.standard_fuses()}
        missing, unexpected, injected = classify_fuses(on_chain, declared, standard)
        if not missing and not unexpected:
            note = f"fuses set matches ({len(declared)} declared"
            if injected:
                note += f" + {len(injected)} standard factory-injected"
            _ok(note + ")")
        else:
            if missing: _fail(f"missing declared fuses on-chain: {missing}")
            if unexpected: _warn(f"unexpected fuses on-chain (not declared, not standard): {unexpected}")
    except Exception as e:
        _fail(f"get_fuses failed: {e}")

    # 2b. FuseWhitelist — every fuse the vault carries must be listed + active on the
    # chain's registry (deprecated standard fuse = the s02b upgrade step is pending).
    try:
        chain_fuses = [a for a in vault.get_fuses().call()]
        standard = {a.lower() for a in deploy_ctx.standard_fuses()}
        statuses = read_fuse_statuses(session.ctx.web3, deploy_ctx.fuse_whitelist,
                                      [(f"fuse[{i}]", a) for i, a in enumerate(chain_fuses)])
        wl_fail, wl_warn = whitelist_problems(statuses)
        # a deprecated *standard* fuse is a pending 02b upgrade, not a broken vault
        std_dep = [x for x in wl_fail if "deprecated" in x and any(a in x.lower() for a in standard)]
        wl_fail = [x for x in wl_fail if x not in std_dep]
        for x in std_dep: _warn(f"standard fuse pending upgrade (02b): {x}")
        for x in wl_warn: _warn(f"whitelist: {x}")
        if wl_fail:
            _fail(f"fuses NOT active on FuseWhitelist {deploy_ctx.fuse_whitelist}: {wl_fail}")
        else:
            _ok(f"all {len(statuses)} on-chain fuses listed on FuseWhitelist ({sum(s.state == 1 for s in statuses)} active)")
    except Exception as e:
        _fail(f"FuseWhitelist check failed: {e}")

    # 3. withdraw window
    try:
        win = int(wm.get_withdraw_window().call())
        cfg_win = int(cfg.raw["withdraw_manager"]["window_seconds"])
        if cfg_win == 0:
            # Instant-only vault: window is intentionally not configured (left at
            # the factory default) and is unused — not a mismatch.
            _ok(f"withdraw_window not configured (instant-only); chain default={win}s")
        elif win == cfg_win:
            _ok(f"withdraw_window={win}s matches")
        else:
            _fail(f"withdraw_window mismatch: chain={win} cfg={cfg_win}")
    except Exception as e:
        _warn(f"withdraw_window read failed: {e}")

    # 4. roles
    granted = 0
    missing = 0
    for grant in cfg.raw["roles"]["grants"]:
        try:
            role_id = resolve_role_id(grant["role"])
            addr = Web3.to_checksum_address(grant["account"])
            status = access.has_role(role_id, addr).call()
            if status.is_member:
                granted += 1
            else:
                missing += 1
        except Exception:
            # Unknown role name or RPC backend lag — count as not-yet-present;
            # verify() never raises, the caller decides on the report.
            missing += 1
    if missing == 0:
        _ok(f"all {granted} role grants present")
    else:
        _warn(f"role grants present={granted}, missing={missing}")

    # 5. price feeds — CRITICAL HAZARD POINT.
    # Check the VAULT'S OWN oracle (the cloned price_manager that the vault prices
    # off), NOT the shared chain middleware, and via getAssetPrice (which resolves
    # own sources + global fallback) — NOT getSourceOfAssetPrice (which is empty
    # even when fallback pricing works, giving a false sense of coverage).
    # An asset that can't be priced here = a BROKEN vault (deposit/withdraw/NAV revert).
    try:
        vault_oracle = Web3.to_checksum_address(
            instance.get("price_manager")
            or vault.get_price_oracle_middleware_address().call()
        )
    except Exception:
        vault_oracle = deploy_ctx.price_oracle_middleware
    feed_ok = 0
    unpriceable = []
    for pf in cfg.raw["price_feeds"]:
        asset = Web3.to_checksum_address(pf["asset"])
        try:
            raw = session.ctx.call(vault_oracle, build_get_asset_price(asset))
            price, _dec = abi_decode(["uint256", "uint256"], bytes(raw))
            if price > 0:
                feed_ok += 1
            else:
                unpriceable.append(asset)
        except Exception:
            unpriceable.append(asset)
    if not unpriceable:
        _ok(f"all {feed_ok} assets price via vault oracle {vault_oracle}")
    else:
        _fail(f"UNPRICEABLE assets via vault oracle {vault_oracle}: {unpriceable} — VAULT IS BROKEN")

    # 5b. dependency balance graph — CRITICAL HAZARD POINT.
    # Derive the COMPLETE expected graph from deploy.graph — the SAME function the
    # builder (s04) uses, so the verifier and the builder cannot disagree by
    # construction. A missing edge silently corrupts NAV.
    try:
        expected = derive_dependency_graph(cfg, deploy_ctx)
        dep_missing = []
        for mid, want in expected.items():
            on_chain = set(vault.get_dependency_balance_graph(mid).call())
            if not want.issubset(on_chain):
                dep_missing.append(f"market {mid}->{sorted(want - on_chain)}")
        if not dep_missing:
            _ok(f"all {len(expected)} dependency-graph edges present (incl. lending->ERC20)")
        else:
            _fail(f"MISSING dependency-graph edges: {dep_missing}")
    except Exception as ex:
        _fail(f"dependency-graph check failed: {ex}")

    # 5b. instant-withdraw queue params (config-level; the on-chain entry mirrors the JSON)
    qp = queue_param_problems(cfg.raw.get("instant_withdrawal", {}).get("order", []))
    if qp:
        _fail(f"instant-withdraw queue params too short for fuse family: {qp}")
    elif cfg.raw.get("instant_withdrawal", {}).get("order"):
        _ok(f"instant-withdraw queue params satisfy every fuse family ({len(cfg.raw['instant_withdrawal']['order'])} entries)")

    # 6. substrates
    sub_ok = 0
    sub_warn = 0
    for entry in cfg.raw["substrates"]:
        market_id = deploy_ctx.market_id(entry["market"])
        try:
            on_chain = set(vault.get_market_substrates(market_id).call())
            expected = set(encode_substrates(entry["encoding"], entry["values"]))
            # Canonical-layout check: a word the DEPLOYED fuse would ignore is a hard
            # failure even if it matches the JSON (the JSON can be wrong too).
            bad = canonical_check(entry["encoding"], list(on_chain)) + canonical_check(entry["encoding"], list(expected))
            if bad:
                _fail(f"NON-CANONICAL {entry['encoding']} substrate(s) on market {entry['market']} ({market_id}): {sorted(set(bad))}")
                sub_warn += 1
                continue
            if expected.issubset(on_chain):
                sub_ok += 1
            else:
                sub_warn += 1
        except Exception:
            sub_warn += 1
    if sub_warn == 0:
        _ok(f"all {sub_ok} substrate sets matched (subset)")
    else:
        _warn(f"substrate mismatches: ok={sub_ok} warn={sub_warn}")

    # 6b. callback handlers — CRITICAL HAZARD POINT for flash-loan strategies.
    # Read from the vault's storage (there is no getter): a missing handler passes
    # every other check and reverts HandlerNotFound() on the first flash loan.
    cb_entries = cfg.raw.get("callback_handlers", [])
    if cb_entries:
        cb_missing = []
        for e in cb_entries:
            try:
                want = deploy_ctx.callback_handler(e["handler"]).lower()
                sender = deploy_ctx.resolve_address(e["sender"])
                word = session.ctx.web3.eth.get_storage_at(
                    Web3.to_checksum_address(vault_addr), storage_slot_for(sender, e["signature"]))
                got = handler_from_slot_word(word)
                if got != want:
                    cb_missing.append(f"{e['handler']} for {e['sender']}:{e['signature']} (on-chain {got})")
            except Exception as ex:
                cb_missing.append(f"{e.get('handler')}:{e.get('signature')} read failed: {ex}")
        if not cb_missing:
            _ok(f"all {len(cb_entries)} callback handlers registered (read from vault storage)")
        else:
            _fail(f"MISSING callback handlers — first flash loan would revert HandlerNotFound(): {cb_missing}")

    # 7. total supply cap — DECIMALS-SAFE check. The cap is in SHARE units
    # (underlying decimals + offset); reading it back in human underlying terms
    # catches the off-by-10^offset trap (a vault once shipped a 0.1 cbBTC cap instead of 10).
    try:
        cap = int(vault.get_total_supply_cap().call())
        dec_raw = session.ctx.call(
            Web3.to_checksum_address(vault_addr),
            function_signature_to_4byte_selector("decimals()"),
        )
        share_dec = int.from_bytes(bytes(dec_raw), "big")
        uncapped = (1 << 256) - 1
        human = cap / (10 ** share_dec) if share_dec else cap
        want = cfg.raw["vault"].get("total_supply_cap_underlying")
        if want not in (None, ""):
            expected = int(Decimal(str(want)) * (Decimal(10) ** share_dec))
            if cap == expected:
                _ok(f"total supply cap = {want} underlying ({cap} raw @ {share_dec} share-decimals)")
            else:
                _fail(f"cap MISMATCH: on-chain {human} underlying ({cap} raw) != expected {want} ({expected} raw)")
        elif cap == uncapped:
            _ok("total supply cap: uncapped (max uint256)")
        else:
            _ok(f"total supply cap = {human} underlying ({cap} raw @ {share_dec} share-decimals)")
    except Exception as e:
        _warn(f"total supply cap read failed: {e}")

    print(f"\nverification: {len(report['ok'])} ok, {len(report['warn'])} warn, {len(report['fail'])} fail")
    return report
