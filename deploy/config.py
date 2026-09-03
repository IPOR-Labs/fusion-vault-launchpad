"""Strategy JSON loader + jsonschema validation."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema  # REQUIRED — schema validation is part of the deploy contract,
# never a silent best-effort. A predictable pipeline must not skip its own gate;
# if this import fails, install deps (see requirements.txt) rather than degrade.


from deploy.swapper import swapper_problems

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "strategy.schema.json"


@dataclass(slots=True)
class StrategyConfig:
    raw: dict[str, Any]
    path: Path

    @property
    def name(self) -> str:
        return self.raw["meta"]["name"]

    @property
    def chain_id(self) -> int:
        return int(self.raw["chain"]["id"])

    @property
    def context_name(self) -> str:
        return self.raw["chain"]["context"]

    @property
    def state_file(self) -> Path:
        return Path(self.raw["execution"]["state_file"])

    def section(self, key: str) -> Any:
        return self.raw.get(key)


def load_strategy(path: str | Path) -> StrategyConfig:
    p = Path(path)
    with p.open() as f:
        data = json.load(f)
    with SCHEMA_PATH.open() as f:
        schema = json.load(f)
    jsonschema.validate(instance=data, schema=schema)
    _semantic_checks(data)
    return StrategyConfig(raw=data, path=p)


def _semantic_checks(cfg: dict[str, Any]) -> None:
    """Cross-field rules the JSON Schema can't easily express."""
    fees = cfg.get("fees", {})
    recipients = fees.get("recipients", [])
    if recipients:
        total = sum(r.get("split_bps", 0) for r in recipients)
        if total != 10000:
            raise ValueError(f"fees.recipients split_bps must sum to 10000 (got {total})")

    wl = cfg.get("whitelist", {})
    if not wl.get("enabled_at_launch", False) and wl.get("initial_accounts"):
        raise ValueError("whitelist disabled at launch but initial_accounts is non-empty")

    tr = cfg.get("transferability", {})
    if tr.get("enabled_at_launch", False) and not tr.get("irreversible_ack", False):
        raise ValueError("transferability.enabled_at_launch requires irreversible_ack=true")

    assets_with_feed = {pf["asset"].lower() for pf in cfg.get("price_feeds", [])}
    underlying = cfg.get("vault", {}).get("underlying", "").lower()
    if underlying and underlying not in assets_with_feed:
        raise ValueError(f"underlying {underlying} missing from price_feeds")

    problems = swapper_problems([f["name"] for f in cfg.get("fuses", [])], cfg.get("substrates", []))
    if problems:
        raise ValueError("universal token swapper: " + "; ".join(problems))
