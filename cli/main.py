#!/usr/bin/env python3
"""CLI Sayrhazi — point d'entree multi-plateforme (stdlib uniquement).

Usage :
    python cli/main.py [--version] | [install] [--project .]
    python cli/main.py check [sayrhazi] [--project .]
    python cli/main.py update [--project .]
    python cli/main.py remove [--project .] [--yes]
    python cli/main.py info [--project .]

Sans commande : installe dans --project (comme la fonction de profil).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

COMMANDS = ("install", "check", "tunda", "update", "remove", "info")


def _version() -> str:
    try:
        return (Path(__file__).resolve().parent.parent / "VERSION").read_text(
            encoding="utf-8").strip()
    except OSError:
        return "?"


def main() -> int:
    parser = argparse.ArgumentParser(description="CLI Sayrhazi")
    parser.add_argument("--version", action="store_true", help="affiche la version")
    parser.add_argument("command", choices=COMMANDS, nargs="?")
    parser.add_argument("--project", default=".")
    parser.add_argument("--yes", action="store_true", help="remove : supprimer vraiment (defaut : simulation)")
    args = parser.parse_args()

    if args.version:
        print(_version())
        return 0
    bare = not args.command
    if bare:
        args.command = "install"

    here = Path(__file__).resolve().parent
    target = here / "commands" / (args.command + ".py")
    cmd = [sys.executable, str(target)]
    if args.command in ("check", "tunda"):
        cmd += ["sayrhazi", "--project", args.project]
    else:
        cmd += ["--project", args.project]
    if args.command == "remove" and args.yes:
        cmd += ["--yes"]
    code = subprocess.run(cmd).returncode
    if bare and code == 0:
        check = [sys.executable, str(here / "commands" / "check.py"),
                 "sayrhazi", "--project", args.project]
        code = subprocess.run(check).returncode
    return code


if __name__ == "__main__":
    sys.exit(main())
