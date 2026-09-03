"""Pure Universal Token Swapper family classification — no chain reads, no SDK import.

Three fuse families share the swapper markets (12 / 1202) and differ ONLY in the
substrate encoding the deployed contract reads. The keeper resolves the family from
the fuse address (UniversalTokenSwapperDetector: ADDRESS / TYPED / SELECTOR); a word
in the wrong layout is silently ignored by the fuse, so every swap reverts with a
"not granted" error. weETH Earn and Apex cbBTC shipped that way (2026-09-02 audit).
This module is the config-time guard: fuse name -> the one encoding it accepts.
"""
from __future__ import annotations

SWAPPER_MARKETS = ("UNIVERSAL_TOKEN_SWAPPER", "UNIVERSAL_TOKEN_SWAPPER_V2")

# fuse name (deploy-context key = ipor-abi name) -> (market key, substrate encoding)
FUSE_FAMILY: dict[str, tuple[str, str]] = {
    # ADDRESS family — legacy: every plain address word is both token and target
    "SwapFuseUniversalTokenSwapper":                    ("UNIVERSAL_TOKEN_SWAPPER", "address"),
    "SwapFuseUniversalTokenSwapperEth":                 ("UNIVERSAL_TOKEN_SWAPPER", "address"),
    "SwapFuseUniversalTokenSwapperEthNoSlippage":       ("UNIVERSAL_TOKEN_SWAPPER", "address"),
    "SwapFuseUniversalTokenSwapperEthNoSlippageV2":     ("UNIVERSAL_TOKEN_SWAPPER_V2", "address"),
    # TYPED family — UniversalTokenSwapperSubstrateLib (Token 1 / Target 2 / Slippage 3)
    "UniversalTokenSwapperFuse":                        ("UNIVERSAL_TOKEN_SWAPPER", "universal_typed_substrate"),
    "UniversalTokenSwapperEthFuse":                     ("UNIVERSAL_TOKEN_SWAPPER", "universal_typed_substrate"),
    "UniversalTokenSwapperFuseV2":                      ("UNIVERSAL_TOKEN_SWAPPER_V2", "universal_typed_substrate"),
    "UniversalTokenSwapperEthFuseV2":                   ("UNIVERSAL_TOKEN_SWAPPER_V2", "universal_typed_substrate"),
    # SELECTOR family — WithVerification: (selector << 224 | target), zero selector = token
    "UniversalTokenSwapperWithVerificationFuse":        ("UNIVERSAL_TOKEN_SWAPPER", "universal_selector_substrate"),
    "SwapFuseUniversalTokenSwapperWithVerification":    ("UNIVERSAL_TOKEN_SWAPPER", "universal_selector_substrate"),
    "SwapFuseUniversalTokenSwapperWithVerificationV2":  ("UNIVERSAL_TOKEN_SWAPPER_V2", "universal_selector_substrate"),
}

BALANCE_FUSES = {
    "UniversalTokenSwapperBalanceFuse",
    "BalanceFuseUniversalTokenSwapper",
    "BalanceFuseUniversalTokenSwapperV2",
}


def _kinds(encoding: str, values: list) -> set[str]:
    if encoding == "address":
        return {"token", "target"}  # legacy: each address is both
    return {str(v.get("kind", "")).lower() for v in values if isinstance(v, dict)}


def swapper_problems(fuse_names: list[str], substrates: list[dict]) -> list[str]:
    """Pure: cross-check declared swapper fuses against the swapper substrate entries.
    Returns human-readable problems (empty = OK). Raised as a config error by
    deploy.config._semantic_checks so a wrong layout never reaches grantMarketSubstrates."""
    problems: list[str] = []
    declared = [n for n in fuse_names if n in FUSE_FAMILY]
    for n in fuse_names:
        if "UniversalTokenSwapper" in n and n not in FUSE_FAMILY and n not in BALANCE_FUSES:
            problems.append(f"fuse '{n}' is a Universal Token Swapper variant of unknown family — "
                            f"add it to deploy.swapper.FUSE_FAMILY (check its substrate layout first)")

    per_market: dict[str, set[str]] = {}
    for n in declared:
        market, enc = FUSE_FAMILY[n]
        per_market.setdefault(market, set()).add(enc)
    for market, encs in per_market.items():
        if len(encs) > 1:
            problems.append(f"{market}: swapper fuses of mixed families {sorted(encs)} — one substrate "
                            f"set cannot satisfy both (keeper flags MixedSubstrateVersions); keep one family")

    for entry in substrates:
        market = entry.get("market")
        if market not in SWAPPER_MARKETS:
            continue
        enc = entry.get("encoding")
        expected = per_market.get(market)
        if not expected:
            problems.append(f"{market}: substrates granted but no swapper fuse declared for that market")
            continue
        if enc not in expected:
            problems.append(f"{market}: encoding '{enc}' does not match the declared fuse family "
                            f"(expects {sorted(expected)})")
            continue
        kinds = _kinds(enc, entry.get("values", []))
        if "token" not in kinds:
            problems.append(f"{market}: no token word granted — tokenIn/tokenOut checks would revert")
        if "target" not in kinds:
            problems.append(f"{market}: no target word granted — every router call would revert")
    return problems
