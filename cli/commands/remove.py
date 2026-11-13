#!/usr/bin/env python3
"""CLI remove — retrait prudent de l'intégration Sayrhazi (stdlib uniquement).

Supprime UNIQUEMENT les fichiers pilotes par le noyau Sayrhazi :

  .opencode/sayrhazi-modules.py     briques partagées (module noyau)
  .opencode/sayrhazi-serveur.py     serveur unifié (dashboard + watcher)
  .opencode/watch-work.py            moteur du bouton Marche/Arrêt
  .opencode/services.py              gestionnaire de services
  .opencode/watch-tui.py             résidu legacy (ancien dashboard terminal)
  .opencode/watch-web.py             résidu legacy (ancien dashboard autonome)
  .opencode/static/                  fichiers du dashboard web (HTML/CSS/JS)
  .opencode/state/                   coordination machine (workflow-state.yaml, clean_stop)
  .opencode/__pycache__/             cache noyau généré par l'exécution de Sayrhazi

`agent/`, `resume/`, `history/`, `backup/`, `features/`, `sayrhazi.yaml`,
`workflow.yaml`, `models.yaml`, `opencode.json`, `AGENTS.md`, `node_modules/`,
`package.json`, `package-lock.json`, `bun.lock` et `.gitignore` ne sont jamais
touchés : ils appartiennent au projet (ou sont surchargeables/partagés avec
l'écosystème OpenCode).
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# Fichiers .py noyau à retirer (copie synchronisée de engine/installer.py ;
# chemins relatifs à `.opencode/`). Inclut les résidus des installs anciennes.
NUCLEUS_PY = (
    "sayrhazi-modules.py",
    "sayrhazi-serveur.py",
    "watch-work.py",
    "services.py",
    "watch-tui.py",
    "watch-web.py",
)

# Dossiers noyau à retirer intégralement.
NUCLEUS_DIRS = (
    "static",
    "state",
    "__pycache__",
)


def noyau_targets(opencode: Path) -> list[Path]:
    targets: list[Path] = []
    for name in NUCLEUS_PY:
        path = opencode / name
        if path.is_file():
            targets.append(path)
    for name in NUCLEUS_DIRS:
        path = opencode / name
        if path.is_dir():
            targets.append(path)
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(description="Retirer Sayrhazi (prudent)")
    parser.add_argument("--project", default=".")
    parser.add_argument("--yes", action="store_true", help="supprimer vraiment")
    args = parser.parse_args()

    root = Path(args.project).resolve()
    opencode = root / ".opencode"
    if not opencode.is_dir():
        print("Rien a retirer (dossier .opencode/ absent).")
        return 3

    targets = noyau_targets(opencode)
    if not targets:
        print("Rien a retirer (aucun fichier noyau trouved).")
        return 3

    print("Fichiers noyau concernes :")
    for t in targets:
        kind = "dossier" if t.is_dir() else "fichier"
        print(f"  {t.relative_to(root)} [{kind}]")

    print()
    print("JAMAIS touches : agent/, resume/, history/, backup/, features/,")
    print("sayrhazi.yaml, workflow.yaml, models.yaml, opencode.json, AGENTS.md,")
    print("node_modules/, package.json, package-lock.json, bun.lock, .gitignore.")
    if not args.yes:
        print()
        print("Simulation : relancez avec --yes pour supprimer.")
        return 0

    removed = 0
    for t in targets:
        try:
            if t.is_dir():
                shutil.rmtree(t)
            else:
                t.unlink()
            removed += 1
        except OSError as exc:
            print(f"ERREUR retirant {t.relative_to(root)} : {exc}")
            return 2
    print(f"Retires : {removed} fichier/dossier(s).")
    print("Pour reinstaller : lancez 'sayrhazi .' depuis le projet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
