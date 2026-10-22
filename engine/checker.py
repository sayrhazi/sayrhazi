#!/usr/bin/env python3
"""Validation sans dépendance externe d'une instance Sayrhazi."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from sayrhazi_config import (
        load_schema,
        parse_simple_yaml,
        validate_against_schema,
    )
    HAS_SCHEMA_CHECK = True
except ImportError:
    HAS_SCHEMA_CHECK = False

REQUIRED_AGENTS = {"ali.md", "bamse.md", "hadji.md", "hifadhui.md", "lawibrahim.md", "zawadi.md"}
REQUIRED_DIRS = ("agent", "resume", "history", "features")
REQUIRED_CONFIG_KEYS = (
    "workflow:",
    "  name: Sayrhazi",
    "project:",
    "  name:",
    "agents:",
    "  architect:",
    "  builder-front:",
    "  builder-back:",
    "  reviewer:",
    "  security:",
    "  qa:",
    "technical:",
    "commands:",
    "quality:",
)


def fail(message: str, errors: list[str]) -> None:
    errors.append(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Valide une installation Sayrhazi")
    parser.add_argument("project", help="Racine du projet a controler")
    args = parser.parse_args()

    root = Path(args.project).resolve()
    install_errors: list[str] = []
    config_notes: list[str] = []
    opencode = root / ".opencode"
    config = opencode / "sayrhazi.yaml"

    if not root.is_dir():
        fail(f"Projet introuvable: {root}", install_errors)
    if not opencode.is_dir():
        fail("Dossier .opencode absent", install_errors)
    if not config.is_file():
        fail(".opencode/sayrhazi.yaml absent", install_errors)
    else:
        text = config.read_text(encoding="utf-8")
        for key in REQUIRED_CONFIG_KEYS:
            if key not in text:
                fail(f"Cle de configuration absente: {key}", install_errors)
        remaining = [(i + 1, line.strip()) for i, line in enumerate(text.splitlines()) if re.search(r"(^|:\s*)A_COMPLETER\b", line)]
        if remaining:
            fail(f"Configuration incomplete : {len(remaining)} valeur(s) A_COMPLETER restante(s).", config_notes)
            for lineno, line in remaining:
                fail(f"  sayrhazi.yaml:{lineno}: {line}", config_notes)
        if re.search(r"^\s*name:\s*$", text, re.MULTILINE):
            fail("project.name est vide", config_notes)
        if HAS_SCHEMA_CHECK:
            schema, schema_err = load_schema(Path(__file__).resolve().parent.parent)
            if schema_err:
                fail(schema_err, install_errors)
            else:
                data, parse_err = parse_simple_yaml(text)
                if parse_err:
                    fail(f"sayrhazi.yaml illisible : {parse_err}", install_errors)
                else:
                    for violation in validate_against_schema(data, schema):
                        if "cle requise manquante" in violation:
                            continue  # deja couvert par les cles requises ci-dessus
                        if "A_COMPLETER" in violation:
                            fail(f"schema : {violation}", config_notes)
                        else:
                            fail(f"schema : {violation}", install_errors)

    agent_dir = opencode / "agent"
    if not agent_dir.is_dir():
        fail("Dossier .opencode/agent absent", install_errors)
    else:
        actual = {p.name for p in agent_dir.glob("*.md")}
        for missing in sorted(REQUIRED_AGENTS - actual):
            fail(f"Agent absent: {missing}", install_errors)

    for directory in REQUIRED_DIRS:
        if not (opencode / directory).is_dir():
            fail(f"Dossier .opencode/{directory} absent", install_errors)

    if not (root / "AGENTS.md").is_file():
        fail("AGENTS.md absent a la racine du projet", install_errors)

    # État (Lot 3) : informatif seulement, ne change jamais le code de sortie.
    state_notes: list[str] = []
    try:
        from state import load_state, validate_state
        state_dir = opencode / "state"
        if not state_dir.is_dir():
            state_notes.append("dossier .opencode/state absent (lance `update sayrhazi` pour le créer)")
        else:
            try:
                current = load_state(root)
            except (ValueError, RuntimeError) as exc:
                current = None
                state_notes.append(f"état illisible : {exc}")
            if current is not None:
                for violation in validate_state(current):
                    state_notes.append(f"état : {violation}")
    except ImportError:
        pass

    # Workflow (contrat Lot 2) : informatif seulement, ne change jamais le code de sortie.
    workflow_notes: list[str] = []
    try:
        from workflow import parse_workflow, validate_workflow
        workflow_file = opencode / "workflow.yaml"
        if not workflow_file.is_file():
            workflow_notes.append("workflow.yaml absent (lance `update sayrhazi` pour le créer)")
        else:
            try:
                wdata, werr = parse_workflow(workflow_file.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                wdata, werr = None, "illisible"
            if werr:
                workflow_notes.append(f"workflow.yaml illisible : {werr}")
            else:
                for violation in validate_workflow(wdata):
                    workflow_notes.append(f"workflow.yaml : {violation}")
    except ImportError:
        pass

    # Rapports (contrat Lot 1) : informatif seulement, ne change jamais le code de sortie.
    report_notes: list[str] = []
    try:
        from report import parse_report, validate_report, validate_task
        from fullstack import read_fullstack, validate_fullstack
        resume_dir = opencode / "resume"
        if resume_dir.is_dir():
            for name in ("plan.txt", "design.txt", "build.txt", "review.txt", "security.txt", "qa.txt", "frontend.txt", "backend.txt"):
                p = resume_dir / name
                if not p.is_file():
                    continue
                try:
                    rdata = parse_report(p.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
                if "task_id" not in rdata:
                    continue  # ancien rapport sans contrat : ignore
                for violation in validate_report(rdata):
                    report_notes.append(f"rapport {name} : {violation}")
                if name == "plan.txt" and "scope" in rdata:
                    for violation in validate_task(rdata):
                        report_notes.append(f"rapport {name} : {violation}")
            p = resume_dir / "fullstack.txt"
            if p.is_file():
                try:
                    headers, sections = read_fullstack(p)
                    for violation in validate_fullstack(headers, sections):
                        report_notes.append(f"rapport fullstack.txt : {violation}")
                except OSError:
                    pass
    except ImportError:
        pass

    if install_errors:
        print("Installation Sayrhazi echouee :")
        for error in install_errors:
            print(f"- {error}")
        return 1

    if config_notes:
        print("Sayrhazi installe avec succes.")
        for note in config_notes:
            print(f"- {note}")
        for note in state_notes:
            print(f"- [état] {note}")
        for note in workflow_notes:
            print(f"- [workflow] {note}")
        for note in report_notes:
            print(f"- [rapport] {note}")
        print("Completez .opencode/sayrhazi.yaml avant utilisation.")
        return 1

    print(f"Sayrhazi installe et configure avec succes : {root}")
    print("Les six agents et les dossiers de suivi sont presents.")
    for note in state_notes:
        print(f"- [état] {note}")
    for note in workflow_notes:
        print(f"- [workflow] {note}")
    for note in report_notes:
        print(f"- [rapport] {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
