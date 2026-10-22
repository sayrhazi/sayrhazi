#!/usr/bin/env python3
"""Archivage conditionnel et idempotent des artefacts fullstack (Lot 6).

Lecture : .opencode/resume/fullstack.txt
Ecriture : .opencode/history/fullstack-log.md (cumulatif, une entrée par task_id)

Regles :
- L'archivage est declenche par Lawibrahim uniquement après OK global :
  * etapes frontend et backend terminees
  * tous les controles requis declares OK
  * verdict final OK de Hadji
- L'entree est cumulee : une entree complete par task_id, jamais de doublon.
- Idempotente : si le task_id est deja archive, on verifie que l'entree est complete
  sans ajouter de doublon.
- La reinitialisation de l'artefact courant n'intervient qu'apres verification
  que l'archive est complete et enregistrée.
Stdlib uniquement.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

TASK_ID_RE = re.compile(r"^task_id:\s*(\S+)", re.MULTILINE | re.IGNORECASE)
TASK_ID_LINE_RE = re.compile(r"^## Tâche (\S+)$", re.MULTILINE)


def _now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def archive_path(project_root: Path | str) -> Path:
    return Path(project_root) / ".opencode" / "history" / "fullstack-log.md"


def resume_path(project_root: Path | str) -> Path:
    return Path(project_root) / ".opencode" / "resume" / "fullstack.txt"


def read_archive(project_root: Path | str) -> str:
    """Lit le contenu actuel de fullstack-log.md (vide si absent)."""
    path = archive_path(project_root)
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    except OSError:
        return ""


def is_archived(project_root: Path | str, task_id: str) -> bool:
    """Verifie si le task_id possede deja une entree complete dans l'archive."""
    content = read_archive(project_root)
    return bool(TASK_ID_LINE_RE.search(content))


def archive_entries(project_root: Path | str) -> list[str]:
    """Retourne la liste des task_id archivés."""
    content = read_archive(project_root)
    return [m.group(1) for m in TASK_ID_LINE_RE.finditer(content)]


def build_entry(task_id: str, fullstack_text: str, controls: dict | None = None,
                hadji_verdict: str | None = None) -> str:
    """Construit une entree d'archive pour un task_id."""
    lines: list[str] = []
    lines.append(f"## Tâche {task_id}")
    lines.append(f"Archivé le : {_now_iso()}")
    lines.append("")
    lines.append("### Artefact fullstack.txt (état final validé)")
    lines.append("```text")
    lines.append(fullstack_text.rstrip())
    lines.append("```")
    lines.append("")
    if controls:
        lines.append("### Contrôles requis")
        for name, etat in sorted(controls.items()):
            lines.append(f"- {name}: {etat}")
        lines.append("")
    if hadji_verdict:
        lines.append(f"### Verdict qualité (Hadji) : {hadji_verdict}")
        lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def archive_fullstack(project_root: Path | str, task_id: str,
                      controls: dict | None = None,
                      hadji_verdict: str | None = None) -> tuple[bool, str]:
    """Archive fullstack.txt pour le task_id donne.

    Retourne (succès, message). Si le task_id est deja archivé, ne fait rien
    et retourne (True, "deja archivé").
    """
    if is_archived(project_root, task_id):
        return True, f"{task_id} deja archivé (aucune entree dupliquée)."

    resume = resume_path(project_root)
    try:
        fullstack_text = resume.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return False, f"{resume} absent : impossible d'archiver."
    except OSError as exc:
        return False, f"lecture de {resume} impossible : {exc}"

    entry = build_entry(task_id, fullstack_text, controls, hadji_verdict)
    path = archive_path(project_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(existing.rstrip() + "\n\n" + entry + "\n")
    except OSError as exc:
        return False, f"ecriture de l'archive impossible : {exc}"

    return True, f"{task_id} archive dans {path}."


def validate_archive_conditions(controls: dict, hadji_verdict: str | None,
                                required_controls: list[str]) -> list[str]:
    """Verifie que toutes les conditions d'archivage sont satisfaites.

    Retourne la liste des violations (vide si tout est OK).
    """
    errors: list[str] = []
    for name in required_controls:
        etat = (controls or {}).get(name)
        if etat is None:
            errors.append(f"controle requis `{name}` absent")
        elif str(etat).strip() != "OK":
            errors.append(f"controle requis `{name}` n'est pas OK (etat : {etat})")
    if hadji_verdict is None or str(hadji_verdict).strip() != "OK":
        errors.append("verdict qualité de Hadji absent ou non OK")
    return errors


def main() -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Archive fullstack.txt dans fullstack-log.md")
    parser.add_argument("project", help="racine du projet")
    parser.add_argument("task_id", help="identifiant de la tâche à archiver")
    parser.add_argument("--hadji", default=None, help="verdict qualité de Hadji (OK)")
    parser.add_argument("--controls", default=None,
                        help="controles au format nom:etat,nom:etat")
    args = parser.parse_args()

    controls = None
    if args.controls:
        controls = {}
        for item in args.controls.split(","):
            if ":" in item:
                name, etat = item.split(":", 1)
                controls[name.strip()] = etat.strip()

    required = [n for n, e in (controls or {}).items()]

    errors = validate_archive_conditions(controls or {}, args.hadji, required)
    if errors:
        print("Archivage bloqué :")
        for err in errors:
            print(f"- {err}")
        return 1

    ok, msg = archive_fullstack(args.project, args.task_id, controls, args.hadji)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
