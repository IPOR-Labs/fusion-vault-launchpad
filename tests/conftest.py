"""Make the repo root importable so `deploy.*` / `tools.*` resolve in tests."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_sessionfinish(session, exitstatus):
    """The unit suite is SDK-free by contract: CI installs no ipor-fusion. The chain-I/O
    modules (deploy.cli, deploy.sdk_session, the step modules) import the SDK at module
    level, so a unit test that imports them passes locally and fails in CI. Catch that
    here, where the SDK happens to be installed. (test_roles may importorskip the SDK
    itself for a cross-check; that is fine.)"""
    offenders = [m for m in sys.modules if m in ("deploy.cli", "deploy.sdk_session") or m.startswith("deploy.steps")]
    if offenders:
        print(f"\nERROR: unit tests imported SDK-dependent modules {offenders}; keep the unit suite SDK-free (CONTRIBUTING.md)")
        session.exitstatus = 1
