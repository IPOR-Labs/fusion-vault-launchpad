"""Idempotent deploy state. Saved as JSON next to the strategy file."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class StepRecord:
    step: str
    tx_hashes: list[str] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DeployState:
    config_hash: str
    completed_steps: list[StepRecord] = field(default_factory=list)
    fusion_instance: dict[str, str] | None = None
    feeds: dict[str, str] = field(default_factory=dict)  # asset -> feed address

    def has_step(self, name: str) -> bool:
        return any(r.step == name for r in self.completed_steps)

    def record(self, name: str, tx_hashes: list[str] | None = None, notes: dict | None = None) -> None:
        if self.has_step(name):
            return
        self.completed_steps.append(StepRecord(step=name, tx_hashes=tx_hashes or [], notes=notes or {}))


def hash_config(raw: dict) -> str:
    payload = json.dumps(raw, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def load_state(path: Path, raw_config: dict, force_restart: bool = False) -> DeployState:
    cfg_hash = hash_config(raw_config)
    if not path.exists() or force_restart:
        return DeployState(config_hash=cfg_hash)
    with path.open() as f:
        data = json.load(f)
    if data.get("config_hash") != cfg_hash and not force_restart:
        raise RuntimeError(
            f"Config drift detected (state={data.get('config_hash')}, current={cfg_hash}). "
            "Use --force-restart to discard prior state."
        )
    steps = [StepRecord(**r) for r in data.get("completed_steps", [])]
    return DeployState(
        config_hash=cfg_hash,
        completed_steps=steps,
        fusion_instance=data.get("fusion_instance"),
        feeds=data.get("feeds", {}),
    )


def save_state(path: Path, state: DeployState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "config_hash": state.config_hash,
        "fusion_instance": state.fusion_instance,
        "feeds": state.feeds,
        "completed_steps": [asdict(r) for r in state.completed_steps],
    }
    with path.open("w") as f:
        json.dump(payload, f, indent=2)
