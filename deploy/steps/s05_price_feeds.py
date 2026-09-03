"""Step 05: deploy price feeds via factories + register in middleware."""
from __future__ import annotations

from eth_abi import decode as abi_decode

from web3 import Web3

from deploy.encoders.feeds import (
    build_collateral_token_morpho_create,
    build_dual_cross_reference_create,
    build_erc4626_create,
    build_middleware_set_asset_prices_sources,
    build_get_source_of_asset_price,
    build_get_asset_price,
)

NAME = "05_price_feeds"


def _impersonate_and_call(session, deploy_ctx, calldata, original_err):
    """Anvil-only fallback: impersonate middleware owner and send the call.

    Production deploy needs the real PRICE_ORACLE_MIDDLEWARE_MANAGER_ROLE holder
    to sign — but on a fork we want to rehearse the end-state regardless.
    """
    w3 = session.ctx.web3
    # check if this is anvil
    try:
        client_ver = w3.client_version
    except Exception:
        client_ver = ""
    if "anvil" not in client_ver.lower() and "hardhat" not in client_ver.lower():
        raise original_err

    # The role-holder on the shared middleware comes from the chain context
    # (`middleware_owner`); without it there is nothing safe to impersonate.
    owner = deploy_ctx.middleware_owner
    if owner is None:
        print(f"[{NAME}]   middleware setter reverted and context '{deploy_ctx.name}' has no "
              f"middleware_owner to impersonate on the fork")
        raise original_err
    print(f"[{NAME}]   middleware setter reverted; impersonating role-holder {owner}")
    w3.provider.make_request("anvil_impersonateAccount", [owner])
    w3.provider.make_request("anvil_setBalance", [owner, hex(10**19)])
    tx = {
        "to": deploy_ctx.price_oracle_middleware,
        "from": owner,
        "data": "0x" + calldata.hex(),
    }
    tx_hash = w3.eth.send_transaction(tx)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    w3.provider.make_request("anvil_stopImpersonatingAccount", [owner])
    if receipt["status"] != 1:
        raise RuntimeError(f"impersonated middleware call failed: {tx_hash.hex()}")
    return tx_hash.hex()


def _deploy_one(cfg, deploy_ctx, session, broadcast, pf):
    asset = Web3.to_checksum_address(pf["asset"])
    feed_type = pf["feed_type"]
    if feed_type == "prebuilt":
        name = pf["feed"]
        addr = deploy_ctx.price_feed_factory(name)
        print(f"[{NAME}] asset={asset} prebuilt feed {name} -> {addr}")
        return addr
    if feed_type == "literal":
        # The source is an existing on-chain feed/aggregator (e.g. a Chainlink
        # USD aggregator) registered directly — no factory deploy needed.
        addr = Web3.to_checksum_address(pf["feed"])
        print(f"[{NAME}] asset={asset} literal feed -> {addr}")
        return addr
    if feed_type == "DualCrossReferencePriceFeedFactory":
        factory = deploy_ctx.price_feed_factory(feed_type)
        params = dict(pf["params"])
        params.setdefault("asset_x", asset)
        calldata = build_dual_cross_reference_create(factory, params)
        if not broadcast:
            print(f"[{NAME}] asset={asset} DualCrossReference -> would call factory {factory}")
            return None
        raw = session.ctx.call(factory, calldata)
        (preview,) = abi_decode(["address"], bytes(raw))
        receipt = session.ctx.send(factory, calldata)
        print(f"[{NAME}] asset={asset} DualCrossReference feed={preview} tx={receipt['transactionHash'].hex()}")
        return Web3.to_checksum_address(preview)
    if feed_type == "CollateralTokenOnMorphoMarketPriceFeedFactory":
        factory = deploy_ctx.price_feed_factory(feed_type)
        params = dict(pf["params"])
        params["price_oracle_middleware"] = deploy_ctx.price_oracle_middleware
        calldata = build_collateral_token_morpho_create(params)
        if not broadcast:
            print(f"[{NAME}] asset={asset} CollateralTokenOnMorpho -> would call factory {factory}")
            return None
        raw = session.ctx.call(factory, calldata)
        (preview,) = abi_decode(["address"], bytes(raw))
        receipt = session.ctx.send(factory, calldata)
        print(f"[{NAME}] asset={asset} CollateralTokenOnMorpho feed={preview} tx={receipt['transactionHash'].hex()}")
        return Web3.to_checksum_address(preview)
    if feed_type == "ERC4626PriceFeedFactory":
        factory = deploy_ctx.price_feed_factory(feed_type)
        params = dict(pf.get("params") or {})
        params.setdefault("asset", asset)
        calldata = build_erc4626_create(params)
        if not broadcast:
            print(f"[{NAME}] asset={asset} ERC4626 -> would call factory {factory}")
            return None
        raw = session.ctx.call(factory, calldata)
        (preview,) = abi_decode(["address"], bytes(raw))
        receipt = session.ctx.send(factory, calldata)
        print(f"[{NAME}] asset={asset} ERC4626 feed={preview} tx={receipt['transactionHash'].hex()}")
        return Web3.to_checksum_address(preview)
    raise ValueError(f"unknown feed_type {feed_type}")


def _prices_ok(session, oracle, asset):
    """True if `oracle` can price `asset` (its own source OR global fallback).

    This is the REAL functional check — an asset with no explicit per-vault
    source can still price via fallback; one that reverts here is unpriceable.
    """
    try:
        raw = session.ctx.call(oracle, build_get_asset_price(asset))
        price, _dec = abi_decode(["uint256", "uint256"], bytes(raw))
        return price > 0
    except Exception:
        return False


def run(cfg, deploy_ctx, session, instance, state, broadcast):
    if state.has_step(NAME):
        print(f"[{NAME}] already done — skipping")
        return

    # CRITICAL: the PlasmaVault prices off its OWN oracle (the cloned price_manager),
    # NOT the shared chain middleware. Register feeds there and verify against it.
    # The shared middleware only matters as a fallback source.
    vault_oracle = Web3.to_checksum_address(
        instance.get("price_manager") or deploy_ctx.price_oracle_middleware
    )
    print(f"[{NAME}] vault oracle (price_manager) = {vault_oracle}")

    assets = []
    sources = []
    recs = []
    for pf in cfg.raw["price_feeds"]:
        asset = Web3.to_checksum_address(pf["asset"])
        rec = session.recorder.add(
            NAME, action="registerPriceFeed", key=asset,
            target=vault_oracle, function="setAssetsPricesSources(address[],address[])",
            args={"asset": asset, "feed_type": pf["feed_type"]},
        )
        if _prices_ok(session, vault_oracle, asset):
            print(f"[{NAME}] asset={asset} already priceable via vault oracle — keeping")
            rec.skipped = True
            rec.note = "already priceable via vault oracle"
            continue
        feed_addr = _deploy_one(cfg, deploy_ctx, session, broadcast, pf)
        if feed_addr is None:
            # Dry-run: a factory-minted feed address is only known at broadcast.
            rec.note = "feed address resolved at broadcast (factory create)"
            continue
        state.feeds[asset.lower()] = feed_addr
        # Resolved feed address is a result, not part of the plan identity — keep
        # it out of `args` so a dry-run plan and a broadcast run match by identity.
        rec.note = f"feed={feed_addr}"
        assets.append(asset)
        sources.append(feed_addr)
        recs.append(rec)

    if assets and broadcast:
        # Register on the VAULT'S oracle — the deployer holds role 1200 there
        # (granted in 01b_bootstrap_roles), so no impersonation is needed.
        calldata = build_middleware_set_asset_prices_sources(assets, sources)
        try:
            receipt = session.ctx.send(vault_oracle, calldata)
            tx_hash = receipt["transactionHash"].hex()
        except Exception as e:
            tx_hash = _impersonate_and_call(session, deploy_ctx, calldata, e)
        print(f"[{NAME}] vault_oracle.setAssetsPricesSources tx={tx_hash}")
        for rec in recs:
            rec.executed = True
            rec.tx_hash = tx_hash
        state.record(NAME, tx_hashes=[tx_hash],
                     notes={"oracle": vault_oracle, "feeds": {a: s for a, s in zip(assets, sources)}})
    elif not assets:
        print(f"[{NAME}] no new feeds to register (all priceable via vault oracle)")
        state.record(NAME)

    # HARD GATE: every valued asset MUST price via the vault's own oracle, or the
    # vault is broken (deposits/withdrawals/NAV revert). Fail loudly — never ship.
    # Only meaningful once the vault actually exists on-chain (broadcast); in a pure
    # dry-run the preview price_manager has no code, so skip.
    if broadcast:
        unpriceable = [
            pf["asset"] for pf in cfg.raw["price_feeds"]
            if not _prices_ok(session, vault_oracle, Web3.to_checksum_address(pf["asset"]))
        ]
        if unpriceable:
            raise RuntimeError(
                f"[{NAME}] CRITICAL HAZARD: assets unpriceable via vault oracle "
                f"{vault_oracle}: {unpriceable}. The vault would be broken — fix feeds "
                f"(register a source on the vault's price_manager) before continuing."
            )
        print(f"[{NAME}] ✅ verified: all {len(cfg.raw['price_feeds'])} assets price via vault oracle")
