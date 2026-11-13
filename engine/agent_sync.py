#!/usr/bin/env python3
"""Synchronisation des fiches agent vers le dossier personnel OpenCode.

OpenCode lit le `model:` de chaque agent dans `~/.opencode/agent/<agent>.md`
au lancement, quel que soit le projet. Quand un modèle (ou tout autre champ
des fiches canoniques) change côté projet, ce dossier doit rester aligné
pour qu'OpenCode lance l'agent avec le bon modèle partout.

`sync_home_opencode(project, core, home=None)` :
  - crée `<home>/.opencode/agent/` si absent (tout environnement, Windows/Linux) ;
  - reproduit les 6 fiches canoniques du noyau (`ali`, `bamse`, `hadji`,
    `hifadhui`, `lawibrahim`, `zawadi`) en prenant, pour chacune, la fiche du
    projet si elle existe (elle porte le choix du projet : modèle, ...), sinon
    celle du noyau ;
  - n'observe ni ne modifie les autres fichiers de `~/.opencode/agent/`
    (agents personnalisés venus d'ailleurs) ;
  - retourne la liste des fiches écrites.

Le projet fait foi : si `.opencode/agent/<agent>.md` porte un modèle précis,
ce modèle sera aussi celui de `~/.opencode/agent/<agent>.md`.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

AGENT_FICHES = ("ali.md", "bamse.md", "hadji.md", "hifadhui.md", "lawibrahim.md", "zawadi.md")


def read_model(path: Path) -> str | None:
    """Lit la valeur de `model:` dans le frontmatter YAML en debut de fichier."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return None
    for line in m.group(1).splitlines():
        mm = re.match(r'^\s*model:\s*"?([^"]+)"?\s*$', line)
        if mm:
            return mm.group(1).strip()
    return None


def write_model(path: Path, model: str) -> None:
    """Remplace ou ajoute la ligne `model:` dans le frontmatter YAML
    sans altérer l'indentation ni ajouter de guillemets."""
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"(---\s*\n)(.*?)(\n---\s*\n)", text, re.DOTALL)
    if not m:
        return
    head, body, tail = m.group(1), m.group(2), m.group(3)
    lines = body.splitlines()
    replaced = False
    for i, line in enumerate(lines):
        mm = re.match(r'^(\s*model:\s*).+?\s*$', line)
        if mm:
            lines[i] = mm.group(1) + model
            replaced = True
            break
    if not replaced:
        lines.insert(0, 'model: ' + model)
    path.write_text(head + "\n".join(lines) + tail + text[m.end():], encoding="utf-8")


def sync_home_opencode(project: Path, core: Path, home: Path | None = None) -> list[str]:
    """Reproduit les fiches canoniques du projet dans `~/.opencode/agent/`.

    `home` permet de surcharger la racine (tests, emplacement non standard) ;
    par défaut c'est `Path.home()`, ce qui fonctionne tel quel sous Windows et
    Linux. Retourne la liste des fiches écrites.
    """
    home_agent = (Path(home) if home else Path.home()) / ".opencode" / "agent"
    home_agent.mkdir(parents=True, exist_ok=True)
    projet_agent = Path(project) / ".opencode" / "agent"
    noyau_agent = Path(core) / "runtimes" / "opencode" / "agents"

    ecrits: list[str] = []
    for name in AGENT_FICHES:
        projet_f = projet_agent / name
        noyau_f = noyau_agent / name
        src = projet_f if projet_f.is_file() else noyau_f
        if not src.is_file():
            continue
        shutil.copy2(src, home_agent / name)
        ecrits.append(name)
    return ecrits


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Synchronise ~/.opencode/agent avec un projet Sayrhazi")
    parser.add_argument("--project", default=".", help="racine du projet (defaut : .)")
    parser.add_argument("--core", default=None,
                        help="racine du noyau Sayrhazi (defaut : ce fichier/..)")
    parser.add_argument("--home", default=None,
                        help="racine du dossier personnel (defaut : Path.home())")
    args = parser.parse_args()
    project = Path(args.project).resolve()
    core = Path(args.core).resolve() if args.core else Path(__file__).resolve().parent.parent
    home = Path(args.home).resolve() if args.home else None
    ecrits = sync_home_opencode(project, core, home)
    if ecrits:
        print("Dossier personnel OpenCode (~/.opencode/agent) synchronise : " + ", ".join(ecrits))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
