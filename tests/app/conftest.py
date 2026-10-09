"""Expose the demo package when pytest is launched via its console script."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
