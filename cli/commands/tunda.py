#!/usr/bin/env python3
"""Tunda — petit check optionnel de la configuration Sayrhazi d'un projet.

Alias pour `sayrhazi check sayrhazi`.
"""
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    check_script = Path(__file__).resolve().parent / "check.py"
    sys.exit(subprocess.run([sys.executable, str(check_script)] + sys.argv[1:]).returncode)