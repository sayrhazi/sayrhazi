#!/usr/bin/env python3
"""CLI info - noyau + projet installe (stdlib uniquement).

Affiche version noyau, version installee, agents presents, rapports presents.
Exit 0 (informatif, jamais bloquant).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "engine"))
from banner import banner  # noqa: E402
from resolver import resolve  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Info Sayrhazi")
    parser.add_argument("--project", default=".")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent.parent
    info = resolve(Path(args.project).resolve(), root)
    print(banner("info sayrhazi", color=sys.stdout.isatty()))
    print("noyau : " + (info["latest"] or "(inconnu)"))
    print("adaptateurs : " + ", ".join(sorted(p.stem for p in (root / "runtimes" / "opencode" / "agents").glob("*.md"))))
    print("projet : " + str(Path(args.project).resolve()))
    print("version installee : " + (info["installed"] or "(absente)"))
    print("statut : " + info["status"])
    opencode = Path(args.project).resolve() / ".opencode"
    for name in ("resume", "history", "features"):
        present = sorted(p.name for p in (opencode / name).glob("*") if p.is_file()) if (opencode / name).is_dir() else []
        print(name + " : " + (", ".join(present) if present else "(vide/absent)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
