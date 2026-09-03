"""Machine-readable run recorder + manifest.

Every step emits its intended on-chain actions through a single `RunRecorder`
attached to the session, so a run produces a structured, diffable artifact
instead of only human-readable stdout:

  * dry-run    -> writes ``<name>.plan.json`` (actions with ``executed=False``)
  * broadcast  -> writes ``<name>.run.json``  (actions with tx hashes + gas)

The recorder is a passive side-channel: ``add()`` only appends data and must
never raise on normal input or influence transaction logic. Steps call it
alongside their existing ``print`` / ``send`` — they do not change signature.

ACTION IDENTITY vs RESULT
-------------------------
An action's *logical identity* is ``(step, action, key)`` — what the step
intends to do, fully determined by the config. ``target`` / ``calldata`` /
``executed`` / ``tx_hash`` / ``gas_used`` are *results* that may only be known
after broadcast (e.g. a price feed whose address a factory mints on-chain).
The plan↔run diff (see ``diff_plan_run``) compares identities and treats result
fields as informational, so a clean rehearsal does not false-alarm on addresses
that are unknowable in a pure dry-run.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _sdk_version() -> str:
    try:
        import ipor_fusion
        return getattr(ipor_fusion, "__version__", "unknown")
    except Exception:
        return "unknown"


@dataclass(slots=True)
class Action:
    step: str
    action: str
    key: str = ""              # stable per-step logical id (fuse name, market id, role+acct, asset…)
    function: str | None = None
    target: str | None = None  # contract address actually called (may be None in dry-run)
    args: dict[str, Any] = field(default_factory=dict)
    calldata: str | None = None
    executed: bool = False
    skipped: bool = False
    note: str | None = None
    tx_hash: str | None = None
    gas_used: int | None = None

    def identity(self) -> tuple[str, str, str, str]:
        """Config-determined logical identity — excludes result fields."""
        return (self.step, self.action, self.key, json.dumps(self.args, sort_keys=True, default=str))


@dataclass(slots=True)
class RunRecorder:
    strategy: str
    config_hash: str
    mode: str                  # "dry-run" | "broadcast"
    chain_id: int
    rpc_source: str
    signer: str
    gas_multiplier: float
    context: str
    actions: list[Action] = field(default_factory=list)

    def add(self, step: str, action: str, **kw: Any) -> Action:
        """Append an action. Never raises on normal input; returns the Action so
        a broadcasting step can fill in tx_hash/gas after ``send()``."""
        a = Action(step=step, action=action, **kw)
        self.actions.append(a)
        return a

    # ---- serialization -------------------------------------------------
    def manifest(self) -> dict[str, Any]:
        """Volatile run metadata — excluded from the plan↔run identity diff."""
        return {
            "strategy": self.strategy,
            "config_hash": self.config_hash,
            "mode": self.mode,
            "chain_id": self.chain_id,
            "rpc_source": self.rpc_source,
            "signer": self.signer,
            "gas_multiplier": self.gas_multiplier,
            "context": self.context,
            "sdk_version": _sdk_version(),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def to_dict(self) -> dict[str, Any]:
        return {"manifest": self.manifest(), "actions": [asdict(a) for a in self.actions]}

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)
        return path


# Fields that are results (post-broadcast or chain-dependent), not part of the
# config-determined plan. Ignored when diffing a plan against a run.
_RESULT_FIELDS = {"target", "calldata", "executed", "skipped", "tx_hash", "gas_used", "note"}


def _identity(action: dict[str, Any]) -> tuple:
    return (
        action["step"],
        action["action"],
        action.get("key", ""),
        json.dumps(action.get("args", {}), sort_keys=True, default=str),
    )


def diff_plan_run(plan: dict[str, Any], run: dict[str, Any]) -> dict[str, list]:
    """Compare a plan artifact against a run artifact by logical identity.

    Returns ``{"matched": [...], "run_only": [...], "plan_only": [...]}``.

    * ``run_only``  — actions broadcast that the plan never declared. This is the
      hard-fail signal: the pipeline did something the deterministic plan did not
      foresee.
    * ``plan_only`` — planned actions absent from the run. Usually benign (the
      broadcast skipped an already-satisfied action, e.g. an asset already
      priceable), but surfaced for review.
    """
    plan_ids: dict[tuple, dict] = {_identity(a): a for a in plan.get("actions", [])}
    run_ids: dict[tuple, dict] = {_identity(a): a for a in run.get("actions", [])}

    matched, run_only, plan_only = [], [], []
    for ident, a in run_ids.items():
        (matched if ident in plan_ids else run_only).append(a)
    for ident, a in plan_ids.items():
        if ident not in run_ids:
            plan_only.append(a)
    return {"matched": matched, "run_only": run_only, "plan_only": plan_only}
