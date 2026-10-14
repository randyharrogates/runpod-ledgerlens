"""Expose the demo package when pytest is launched via its console script."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest


@pytest.fixture(autouse=True)
def _no_worker_log_poller(monkeypatch):
    """The live worker-log poller is on by default; tests must never reach Runpod."""
    monkeypatch.setenv("RUNPOD_WORKER_LOGS", "0")
