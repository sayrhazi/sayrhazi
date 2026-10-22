#!/usr/bin/env python3
"""Gestion de l'artefact partagé .opencode/resume/fullstack.txt (Lot 3).

Structure :
  task_id: FEATURE-012
  scope: fullstack
  status: EN COURS

  ## Étape frontend — Ali
  agent: frontend
  stage_status: TERMINÉ
  completed_at: "2026-10-21T10:00:00Z"
  summary: ...

  ## Étape backend — Bamse
  agent: backend
  stage_status: EN COURS
  completed_at: "2026-10-21T11:00:00Z"
  summary: ...

Règle : chaque agent ne met à jour que sa propre section. L'autre section
reste intacte. Les en-têtes (task_id, scope, status) sont partagés.
Stdlib uniquement.
"""
from __future__ import annotations

import re
from pathlib import Path

SECTION_HEADER_RE = re.compile(r"^## Étape (frontend|backend) — (Ali|Bamse)$")
SECTION_AGENT = {"frontend": "frontend", "backend": "backend"}
VALID_SECTIONS = ("frontend", "backend")


class FullstackError(ValueError):
    """Erreur de structure de fullstack.txt."""


def _split_sections(text: str) -> tuple[dict, dict[str, list[str]]]:
    """Sépare les en-têtes des sections.

    Retourne (headers, sections) où headers est un dict clé->valeur et
    sections un dict nom_section -> liste de lignes (sans l'en-tête ##).
    """
    headers: dict = {}
    sections: dict[str, list[str]] = {name: [] for name in VALID_SECTIONS}
    current: str | None = None
    for raw in text.splitlines():
        match = SECTION_HEADER_RE.match(raw.strip())
        if match:
            current = match.group(1)
            continue
        if current is None:
            if ":" in raw and not raw.strip().startswith("- "):
                key, _, value = raw.partition(":")
                headers[key.strip()] = value.strip()
        else:
            sections[current].append(raw)
    return headers, sections


def read_fullstack(path: Path) -> tuple[dict, dict[str, list[str]]]:
    """Lit fullstack.txt. Retourne (headers, sections)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")
    except FileNotFoundError:
        return {}, {name: [] for name in VALID_SECTIONS}
    except OSError as exc:
        raise FullstackError(f"illisible : {exc}")
    return _split_sections(text)


def write_fullstack(
    path: Path,
    headers: dict,
    sections: dict[str, list[str]],
) -> None:
    """Écrit fullstack.txt en reconstruisant entièrement depuis headers + sections."""
    lines: list[str] = []
    for key in ("task_id", "scope", "status"):
        if key in headers:
            lines.append(f"{key}: {headers[key]}")
    for key, value in headers.items():
        if key not in ("task_id", "scope", "status"):
            lines.append(f"{key}: {value}")
    lines.append("")
    for section in VALID_SECTIONS:
        agent_name = "Ali" if section == "frontend" else "Bamse"
        lines.append(f"## Étape {section} — {agent_name}")
        lines.extend(sections.get(section, []))
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_section(
    path: Path,
    section: str,
    new_lines: list[str],
) -> tuple[dict, dict[str, list[str]]]:
    """Met à jour uniquement la section `section` avec `new_lines`.

    Les en-têtes et l'autre section sont préservés. Retourne
    (headers, sections) après mise à jour.
    """
    if section not in VALID_SECTIONS:
        raise FullstackError(f"section inconnue : {section}")
    headers, sections = read_fullstack(path)
    sections[section] = list(new_lines)
    write_fullstack(path, headers, sections)
    return headers, sections


def validate_fullstack(headers: dict, sections: dict[str, list[str]]) -> list[str]:
    """Valide la structure de fullstack.txt. Retourne les violations."""
    errors: list[str] = []
    task_id = headers.get("task_id", "")
    if not task_id:
        errors.append("task_id manquant dans l'en-tête de fullstack.txt")
    scope = headers.get("scope", "")
    if scope != "fullstack":
        errors.append(f"scope doit valoir 'fullstack' (reçu : {scope!r})")
    for name in VALID_SECTIONS:
        lines = sections.get(name, [])
        if not any(l.strip().startswith("agent:") for l in lines):
            errors.append(f"section {name} : champ 'agent' manquant")
        if not any(l.strip().startswith("stage_status:") for l in lines):
            errors.append(f"section {name} : champ 'stage_status' manquant")
    return errors


def main() -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Lit ou valide fullstack.txt")
    parser.add_argument("project", help="racine du projet")
    parser.add_argument("--validate", action="store_true", help="valide la structure")
    args = parser.parse_args()

    path = Path(args.project) / ".opencode" / "resume" / "fullstack.txt"
    headers, sections = read_fullstack(path)
    if args.validate:
        errors = validate_fullstack(headers, sections)
        if errors:
            print("fullstack.txt non conforme :")
            for err in errors:
                print(f"- {err}")
            return 1
        print("fullstack.txt conforme.")
    else:
        print(f"task_id: {headers.get('task_id', '-')}")
        print(f"scope: {headers.get('scope', '-')}")
        print(f"status: {headers.get('status', '-')}")
        for name in VALID_SECTIONS:
            agent_line = next((l for l in sections[name] if l.strip().startswith("agent:")), "agent: absent")
            status_line = next((l for l in sections[name] if l.strip().startswith("stage_status:")), "stage_status: absent")
            print(f"  {name}: {agent_line} / {status_line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
