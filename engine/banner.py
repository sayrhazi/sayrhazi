#!/usr/bin/env python3
"""Banner Sayrhazi - ASCII pur (console Windows PS 5.1), stdlib uniquement.

Affiche uniquement via `info sayrhazi` : les autres commandes restent
analysables en sortie (lignes OK/KO). Version lue depuis VERSION.
"""
from __future__ import annotations

from pathlib import Path

ART = r"""  ____                   _                 _
 / ___|  __ _ _   _ _ __| |__   __ _ ____ (_)
 \___ \ / _` | | | | '__| '_ \ / _` |_  / | |
  ___) | (_| | |_| | |  | | | | (_| |/ /__| |
 |____/ \__,_|\__, |_|  |_| |_|\__,_/_____|_|
              |___/  v{version} - {command}"""

# #269CD8 en ANSI truecolor (38;2;38;156;216), reset ensuite.
BLUE = "\x1b[38;2;38;156;216m"
RESET = "\x1b[0m"


def core_root() -> Path | None:
    try:
        root = Path(__file__).resolve().parent.parent
    except NameError:
        return None
    return root if (root / "VERSION").is_file() else None


def banner(command: str = "info sayrhazi", color: bool = False) -> str:
    version = "?"
    root = core_root()
    if root is not None:
        try:
            version = (root / "VERSION").read_text(encoding="utf-8").strip()
        except OSError:
            pass
    text = ART.format(version=version, command=command)
    return f"{BLUE}{text}{RESET}" if color else text


if __name__ == "__main__":
    import sys
    print(banner(sys.argv[1] if len(sys.argv) > 1 else "info sayrhazi",
                 color=sys.stdout.isatty()))
