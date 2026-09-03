"""Raw calldata builders for price-feed factories + middleware registration."""
from __future__ import annotations

from typing import Any

from eth_abi import encode as abi_encode, decode as abi_decode
from eth_utils import function_signature_to_4byte_selector
from eth_typing import ChecksumAddress
from web3 import Web3


def _selector(sig: str) -> bytes:
    return function_signature_to_4byte_selector(sig)


def build_dual_cross_reference_create(factory: ChecksumAddress, params: dict) -> bytes:
    """DualCrossReferencePriceFeedFactory.create(assetX, assetXAssetYOracleFeed, assetYUsdOracleFeed) -> address."""
    asset_x = Web3.to_checksum_address(params["asset"] if "asset" in params else params.get("asset_x"))
    feed_a = Web3.to_checksum_address(params["feed_a"])
    feed_b = Web3.to_checksum_address(params["feed_b"])
    sig = "create(address,address,address)"
    return _selector(sig) + abi_encode(["address", "address", "address"], [asset_x, feed_a, feed_b])


def decode_dual_cross_reference_create_result(data: bytes) -> ChecksumAddress:
    (addr,) = abi_decode(["address"], data)
    return Web3.to_checksum_address(addr)


def build_collateral_token_morpho_create(params: dict) -> bytes:
    """CollateralTokenOnMorphoMarketPriceFeedFactory.createPriceFeed(
        morphoOracle, collateralToken, loanToken, priceOracleMiddleware
    ) -> address."""
    sig = "createPriceFeed(address,address,address,address)"
    return _selector(sig) + abi_encode(
        ["address", "address", "address", "address"],
        [
            Web3.to_checksum_address(params["morpho_oracle"]),
            Web3.to_checksum_address(params["collateral_token"]),
            Web3.to_checksum_address(params["loan_token"]),
            Web3.to_checksum_address(params["price_oracle_middleware"]),
        ],
    )


def decode_collateral_create_result(data: bytes) -> ChecksumAddress:
    (addr,) = abi_decode(["address"], data)
    return Web3.to_checksum_address(addr)


def build_erc4626_create(params: dict) -> bytes:
    """ERC4626PriceFeedFactory.create(asset) -> address."""
    sig = "create(address)"
    return _selector(sig) + abi_encode(["address"], [Web3.to_checksum_address(params["asset"])])


def build_middleware_set_asset_prices_sources(
    assets: list[ChecksumAddress], sources: list[ChecksumAddress]
) -> bytes:
    """PriceOracleMiddlewareUsdWithRoles.setAssetsPricesSources(address[],address[])."""
    if len(assets) != len(sources):
        raise ValueError("assets and sources must match length")
    sig = "setAssetsPricesSources(address[],address[])"
    return _selector(sig) + abi_encode(["address[]", "address[]"], [assets, sources])


def build_get_source_of_asset_price(asset: ChecksumAddress) -> bytes:
    sig = "getSourceOfAssetPrice(address)"
    return _selector(sig) + abi_encode(["address"], [asset])


def build_get_asset_price(asset: ChecksumAddress) -> bytes:
    """getAssetPrice(address) -> (uint256 price, uint256 decimals).

    This is the REAL pricing check — it resolves through the (per-vault) oracle's
    own sources AND any global-middleware fallback. An asset that has no explicit
    `getSourceOfAssetPrice` can still price here via fallback; an asset that
    reverts here is genuinely unpriceable and the vault would be broken.
    """
    sig = "getAssetPrice(address)"
    return _selector(sig) + abi_encode(["address"], [asset])
