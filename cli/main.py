#!/usr/bin/env python3
"""CLI Sayrhazi - point d'entree multi-plateforme (stdlib uniquement).

Usage :
    python cli/main.py [--version] | [install] [chemin] [--project .]
    python cli/main.py check [chemin] [--project .]
    python cli/main.py update [chemin] [--project .]
    python cli/main.py remove [chemin] [--project .] [--yes]
    python cli/main.py info [chemin] [--project .]
    python cli/main.py server [--port PORT] [--root CHEMIN]

Sans commande ou avec un chemin seul : installe dans ce chemin puis contrôle.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

COMMANDS = ("install", "check", "tunda", "update", "remove", "info", "server")


def _version() -> str:
    try:
        return (Path(__file__).resolve().parent.parent / "VERSION").read_text(
            encoding="utf-8").strip()
    except OSError:
        return "?"


def _run_subprocess(cmd: list[str]) -> int:
    """Lance une commande et retourne son code de sortie.

    Sur KeyboardInterrupt (Ctrl+C), arrete proprement le processus fils
    pour ne pas laisser le terminal dans un etat incoherent (PowerShell).
    """
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(cmd, creationflags=creationflags)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        print("\nInterruption : processus arrete.")
        return 130


def _is_project_path(token: str) -> bool:
    if token in (".", "..") or token.startswith("~") or "/" in token or "\\" in token:
        return True
    return Path(token).is_dir()


def main() -> int:
    parser = argparse.ArgumentParser(description="CLI Sayrhazi")
    parser.add_argument("--version", action="store_true", help="affiche la version")
    parser.add_argument("command", nargs="?", help="commande ou chemin du projet")
    parser.add_argument("path", nargs="?", help="chemin du projet")
    parser.add_argument("--project", default=None, help="racine du projet (defaut: .)")
    parser.add_argument("--yes", action="store_true", help="remove : supprimer vraiment (defaut : simulation)")
    parser.add_argument("--port", type=int, default=None, help="server : port d'ecoute")
    parser.add_argument("--root", type=str, default=None, help="server : racine du projet")
    args = parser.parse_args()

    if args.version:
        print(_version())
        return 0

    bare = args.command is None or (
        args.command not in COMMANDS and _is_project_path(args.command))
    if bare:
        if args.path is not None:
            parser.error("trop d'arguments pour l'installation")
        command = "install"
        project = args.command or args.project or "."
    else:
        if args.command not in COMMANDS:
            parser.error("commande inconnue : " + args.command)
        command = args.command
        project = args.path or args.project or "."

    here = Path(__file__).resolve().parent
    target = here / "commands" / (command + ".py")
    cmd = [sys.executable, str(target)]
    if command == "server":
        if args.port is not None:
            cmd += ["--port", str(args.port)]
        root = args.path or args.root or args.project
        if root is not None:
            cmd += ["--root", root]
    else:
        cmd += ["--project", project]
        if command == "remove" and args.yes:
            cmd += ["--yes"]
    code = _run_subprocess(cmd)
    if bare and code == 0:
        check = [sys.executable, str(here / "commands" / "check.py"),
                 "--project", project]
        code = _run_subprocess(check)
    return code


if __name__ == "__main__":
    sys.exit(main())
