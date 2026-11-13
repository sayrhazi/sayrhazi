#!/usr/bin/env python3
"""Installer Sayrhazi — port Python de Install-Sayrhazi.ps1 (multi-plateforme).

Cree .opencode/agent|resume|history|features|state|backup, recopie agents + watcher
(Force), cree AGENTS.md / sayrhazi.yaml / opencode.json uniquement si
absents. Ne supprime ni ne remplace rapports, historiques ou config.
Synchronise ensuite le dossier personnel OpenCode (~/.opencode/agent) avec
le projet : c'est là qu'OpenCode lit le `model:` au lancement.

Usage :
    python engine/installer.py --project <racine>
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_sync import sync_home_opencode


def install_project(project: Path, core: Path) -> None:
    opencode = project / ".opencode"
    agent_target = opencode / "agent"
    for d in (agent_target, opencode / "resume", opencode / "history", opencode / "features",
              opencode / "state", opencode / "backup"):
        d.mkdir(parents=True, exist_ok=True)

    adapter_agents = core / "runtimes" / "opencode" / "agents"
    for src in sorted(adapter_agents.glob("*.md")):
        shutil.copy2(src, agent_target / src.name)

    templates = core / "runtimes" / "opencode" / "templates"
    for name, dest in (("AGENTS.md", project / "AGENTS.md"),
                       ("sayrhazi.yaml", opencode / "sayrhazi.yaml"),
                       ("opencode.json", opencode / "opencode.json"),
                       ("workflow.yaml", opencode / "workflow.yaml")):
        if not dest.is_file():
            shutil.copy2(templates / name, dest)

    scripts = core / "runtimes" / "opencode" / "scripts"
    # sayrhazi-modules.py doit exister AVANT le serveur (importé par
    # sayrhazi-serveur.py) ; le dashboard web passe par sayrhazi-serveur.py.
    for name in ("watch-work.py", "services.py", "sayrhazi-modules.py",
                 "sayrhazi-serveur.py"):
        src = scripts / name
        if src.is_file():
            shutil.copy2(src, opencode / name)

    static_src = scripts / "static"
    static_dst = opencode / "static"
    if static_src.is_dir():
        for src in sorted(static_src.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(static_src)
            dst = static_dst / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    # Synchroniser le dossier personnel OpenCode (~/.opencode/agent) avec le
    # projet : OpenCode y lit le `model:` de chaque agent au lancement. La
    # fiche du projet fait foi (model inclus). Fonctionne sur tout
    # environnement (Path.home()). Un échec de la synchronisation ne doit pas
    # faire échouer l'installation.
    try:
        ecrits = sync_home_opencode(project, core)
        if ecrits:
            print("Dossier personnel OpenCode (~/.opencode/agent) synchronise : "
                  + ", ".join(ecrits))
    except OSError as exc:
        print("Avertissement : synchronisation de ~/.opencode/agent impossible (" + str(exc) + ")")

    # Registre des fenetres de contexte (dashboard uniquement). Jamais ecrase :
    # un projet peut declarer sa propre table de modeles, comme le fait deja
    # l'updater et les deux installeurs shell/PowerShell.
    models = core / "core" / "runtime" / "models.yaml"
    if models.is_file() and not (opencode / "models.yaml").is_file():
        shutil.copy2(models, opencode / "models.yaml")


def main() -> int:
    parser = argparse.ArgumentParser(description="Installer Sayrhazi")
    parser.add_argument("--project", default=".", help="racine du projet cible")
    args = parser.parse_args()

    try:
        core = Path(__file__).resolve().parent.parent
    except NameError:
        print("ERREUR : noyau introuvable.")
        return 2
    if not (core / "runtimes" / "opencode" / "templates" / "sayrhazi.yaml").is_file():
        print("ERREUR : noyau incomplet (templates/sayrhazi.yaml absent).")
        return 2
    project = Path(args.project).resolve()
    if not project.is_dir():
        print("ERREUR : projet introuvable : " + str(project))
        return 2
    install_project(project, core)
    print("Sayrhazi installe dans : " + str(project))
    print("A completer avant usage : " + str(project / ".opencode" / "sayrhazi.yaml"))
    print("Aucun conteneur Docker n'a ete cree. Docker reste une decision du projet applicatif.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
