#!/usr/bin/env python3
"""CLI server — lance le serveur unifie Sayrhazi (dashboard + watcher).

Usage :
    python cli/main.py server [--port PORT] [--root CHEMIN]
    sayrhazi server [--port PORT] [--root CHEMIN]

Sans --root : remonte depuis le repertoire courant jusqu'a trouver
.opencode/sayrhazi-serveur.py (projet installe).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


def find_project_root(start: Path) -> Path | None:
    """Remonte depuis start pour trouver .opencode/sayrhazi-serveur.py."""
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".opencode" / "sayrhazi-serveur.py").is_file():
            return candidate
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lance le serveur unifie Sayrhazi (dashboard web + watcher)"
    )
    parser.add_argument("--port", type=int, default=8363, help="Port d'ecoute")
    parser.add_argument(
        "--root", type=str, default=None,
        help="Racine du projet Sayrhazi (défaut : detectee depuis le repertoire courant)",
    )
    args = parser.parse_args()

    if args.root:
        root = Path(args.root).resolve()
    else:
        root = find_project_root(Path.cwd())
    if root is None:
        print("ERREUR : aucun projet Sayrhazi installe (.opencode/sayrhazi-serveur.py introuvable).")
        print("       Lancez d'abord 'sayrhazi <projet>' puis 'sayrhazi update'.")
        print("       Utilisez --root <chemin> pour specifier la racine manuellement.")
        return 2

    server = root / ".opencode" / "sayrhazi-serveur.py"
    if not server.is_file():
        print(f"ERREUR : {server} absent.")
        print("       Relancez 'sayrhazi update' pour recuperer le dernier noyau.")
        return 2

    cmd = [sys.executable, str(server), "--port", str(args.port)]
    if args.root:
        cmd += ["--root", str(root)]

    proc = subprocess.Popen(cmd, cwd=str(root))
    try:
        while True:
            ret = proc.poll()
            if ret is not None:
                return ret
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nInterruption : envoi du signal d'arrêt au serveur...")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        return 1
    return proc.returncode if proc.returncode is not None else 0


if __name__ == "__main__":
    sys.exit(main())
