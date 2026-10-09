"""Make the repository worker importable without installing it in the wheel."""

import os
import sys
from pathlib import Path

os.environ["LEDGERLENS_NO_ENGINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
