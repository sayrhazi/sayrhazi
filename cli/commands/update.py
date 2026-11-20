#!/usr/bin/env python3
"""CLI update - delegue a engine/updater.py."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    engine = Path(__file__).resolve().parent.parent.parent / "engine" / "updater.py"
    sys.exit(subprocess.run([sys.executable, str(engine)] + sys.argv[1:]).returncode)
