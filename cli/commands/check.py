#!/usr/bin/env python3
"""Tunda — petit check optionnel de la configuration Sayrhazi d'un projet.

Usage :
    python tunda.py sayrhazi [--project .]
    tunda sayrhazi        (via alias PowerShell, depuis n'importe quel projet)

Sorties ASCII uniquement (console Windows PS 5.1). Exit 0 = VALIDE, 1 = INVALIDE, 2 = NON INSTALLE.
Compare aussi la version installee au noyau (simple invitation a `update sayrhazi`, jamais bloquant).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "engine"))
try:
    from sayrhazi_config import (
        load_schema,
        parse_simple_yaml,
        validate_against_schema,
    )
    HAS_SCHEMA_CHECK = True
except ImportError:
    HAS_SCHEMA_CHECK = False
try:
    from report import parse_report, validate_report, validate_task
    HAS_REPORT_CHECK = True
except ImportError:
    HAS_REPORT_CHECK = False
try:
    from workflow import parse_workflow, summarize_agents, validate_workflow
    HAS_WORKFLOW_CHECK = True
except ImportError:
    HAS_WORKFLOW_CHECK = False
try:
    from state import load_state, validate_state
    HAS_STATE_CHECK = True
except ImportError:
    HAS_STATE_CHECK = False
try:
    from fullstack import read_fullstack, validate_fullstack
    HAS_FULLSTACK_CHECK = True
except ImportError:
    HAS_FULLSTACK_CHECK = False
try:
    from archive import validate_archive_conditions, is_archived, archive_entries
    HAS_ARCHIVE_CHECK = True
except ImportError:
    HAS_ARCHIVE_CHECK = False

REQUIRED_AGENTS = ("ali.md", "bamse.md", "hadji.md", "hifadhui.md", "lawibrahim.md", "zawadi.md")
REQUIRED_DIRS = ("agent", "resume", "history", "features")
REQUIRED_CONFIG_KEYS = ("workflow:", "  name: Sayrhazi", "project:", "  name:", "agents:", "  builder-front:", "  builder-back:", "technical:", "commands:", "quality:")
VERSION_RE = re.compile(r'^\s*version:\s*"([^"]+)"\s*(#.*)?$', re.MULTILINE)


def _core_template() -> Path | None:
    """Localise templates/sayrhazi.yaml du noyau via l'emplacement de ce script."""
    try:
        candidate = (Path(__file__).resolve().parent.parent.parent / "runtimes" / "opencode" / "templates" / "sayrhazi.yaml")
    except NameError:
        return None
    return candidate if candidate.is_file() else None


def _read_workflow_version(yaml_path: Path | None) -> str | None:
    if yaml_path is None or not yaml_path.is_file():
        return None
    try:
        text = yaml_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = VERSION_RE.search(text)
    return m.group(1).strip() if m else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Tunda Sayrhazi config check")
    parser.add_argument("sub", nargs="?", default="sayrhazi", help="sous-commande (seule 'sayrhazi' supportee)")
    parser.add_argument("--project", default=".", help="racine du projet a controler")
    args = parser.parse_args()

    if args.sub != "sayrhazi":
        print(f"[KO] sous-commande inconnue: {args.sub} (usage: tunda sayrhazi)")
        return 1

    root = Path(args.project).resolve()
    opencode = root / ".opencode"
    problems: list[str] = []
    warns: list[str] = []

    def ok(msg: str) -> None:
        print(f"[OK] {msg}")

    def ko(msg: str) -> None:
        print(f"[KO] {msg}")
        problems.append(msg)

    def warn(msg: str) -> None:
        print(f"[WARN] {msg}")
        warns.append(msg)

    print(f"Tunda sayrhazi — projet : {root}")

    # 0. Porte d'entree : Sayrhazi est-il initialise dans ce projet ?
    init_markers = {
        ".opencode/sayrhazi.yaml": (opencode / "sayrhazi.yaml").is_file(),
        ".opencode/agent": (opencode / "agent").is_dir(),
        "AGENTS.md": (root / "AGENTS.md").is_file(),
    }
    if not all(init_markers.values()):
        print("[KO] Sayrhazi n'est pas initialise dans ce projet.")
        for marker, present in init_markers.items():
            if not present:
                print(f"  manquant : {marker}")
        print("Veuillez integrer Sayrhazi avec `sayrhazi`, puis completer .opencode/sayrhazi.yaml.")
        print("Tunda sayrhazi: NON INSTALLE")
        return 2

    # 1. Structure
    ok(".opencode present")

    for d in REQUIRED_DIRS:
        if (opencode / d).is_dir():
            ok(f".opencode/{d} present")
        else:
            ko(f".opencode/{d} absent")

    # 2. Agents
    agent_dir = opencode / "agent"
    if agent_dir.is_dir():
        actual = {p.name for p in agent_dir.glob("*.md")}
        missing = [a for a in REQUIRED_AGENTS if a not in actual]
        if not missing:
            ok(f"{len(REQUIRED_AGENTS)} agents presents")
        else:
            for m in missing:
                ko(f"agent absent: {m}")
    else:
        ko(".opencode/agent absent")

    # 3. AGENTS.md
    agents_md = root / "AGENTS.md"
    if agents_md.is_file():
        ok("AGENTS.md present")
        try:
            am = agents_md.read_text(encoding="utf-8", errors="replace")
            am_sans_code = re.sub(r"`[^`]*`", "", am)
            if "A_COMPLETER" in am_sans_code:
                ko("AGENTS.md contient encore A_COMPLETER")
            if re.search(r"^\s*-\s*\.\.\.\s*$", am, re.MULTILINE):
                warn("AGENTS.md §4 local non rempli ('- ...' restant)")
        except OSError as exc:
            ko(f"AGENTS.md illisible: {exc}")
    else:
        ko("AGENTS.md absent a la racine du projet")

    # 4. sayrhazi.yaml
    config = opencode / "sayrhazi.yaml"
    if not config.is_file():
        ko(".opencode/sayrhazi.yaml absent")
    else:
        try:
            text = config.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            ko(f"sayrhazi.yaml illisible: {exc}")
            text = ""
        if text:
            for key in REQUIRED_CONFIG_KEYS:
                if key in text:
                    ok(f"cle config presente: {key.strip()}")
                else:
                    ko(f"cle config absente: {key.strip()}")
            remaining = [(i + 1, l.strip()) for i, l in enumerate(text.splitlines()) if re.search(r"(^|:\s*)A_COMPLETER\b", l)]
            if not remaining:
                ok("0 A_COMPLETER dans sayrhazi.yaml")
            else:
                ko(f"{len(remaining)} A_COMPLETER restants dans sayrhazi.yaml:")
                for lineno, line in remaining:
                    print(f"  sayrhazi.yaml:{lineno}: {line}")
            if re.search(r"^\s*name:\s*$", text, re.MULTILINE):
                ko("project.name est vide")
            if HAS_SCHEMA_CHECK:
                schema, schema_err = load_schema(Path(__file__).resolve().parent.parent.parent)
                if schema_err:
                    ko(schema_err)
                else:
                    data, parse_err = parse_simple_yaml(text)
                    if parse_err:
                        ko(f"sayrhazi.yaml illisible : {parse_err}")
                    else:
                        violations = [v for v in validate_against_schema(data, schema)
                                      if "cle requise manquante" not in v and "A_COMPLETER" not in v]
                        if violations:
                            for v in violations:
                                ko(f"schema : {v}")
                        else:
                            ok("schema conforme")

    # 5. opencode.json
    oj = opencode / "opencode.json"
    if not oj.is_file():
        ko(".opencode/opencode.json absent")
    else:
        try:
            json.loads(oj.read_text(encoding="utf-8", errors="replace"))
            ok("opencode.json JSON valide")
        except (OSError, json.JSONDecodeError) as exc:
            ko(f"opencode.json invalide: {exc}")

    # 6. watcher (recommande, non bloquant strict)
    if (opencode / "watch-work.py").is_file():
        ok("watch-work.py present")
    else:
        warn("watch-work.py absent (relance Sayrhazi-Install pour l'ajouter)")

    # 6b. gestionnaire de services (recommande, non bloquant strict)
    if (opencode / "services.py").is_file():
        ok("services.py present")
    else:
        warn("services.py absent (lance `update sayrhazi` pour l'ajouter)")

    # 6c. dashboard (optionnel : necessite textual). Verifie la presence du
    # script ET la dependance Python : sans cela, `sayrhazi-tui` echoue sur une
    # erreur d'import opaque, sans que rien ne l'ait annonce au tunda.
    tui = opencode / "watch-tui.py"
    if not tui.is_file():
        warn("watch-tui.py absent (lance `update sayrhazi` pour l'ajouter)")
    else:
        ok("watch-tui.py present")
        try:
            import importlib.util
            if importlib.util.find_spec("textual") is None:
                warn("dashboard inutilisable : 'textual' absent "
                     f"({'pip' if sys.platform == 'win32' else 'pip3'} install textual)")
        except (ImportError, ValueError):
            warn("dashboard inutilisable : 'textual' absent "
                 f"({'pip' if sys.platform == 'win32' else 'pip3'} install textual)")

    # 6d. registre des fenetres de contexte (dashboard)
    if (opencode / "models.yaml").is_file():
        ok("models.yaml present (fenetres de contexte)")
    else:
        warn("models.yaml absent : le dashboard affichera n/d sur le contexte "
             "(lance `update sayrhazi`)")

    # 8. rapports (contrat Lot 1 : informatif seulement, jamais bloquant)
    if HAS_REPORT_CHECK:
        checked = 0
        bad = 0
        for name in ("plan.txt", "design.txt", "build.txt", "review.txt", "security.txt", "qa.txt",
                     "frontend.txt", "backend.txt"):
            p = opencode / "resume" / name
            if not p.is_file():
                continue
            try:
                rdata = parse_report(p.read_text(encoding="utf-8", errors="replace"))
            except OSError as exc:
                warn(f"rapport {name} illisible: {exc}")
                continue
            if "task_id" not in rdata:
                continue  # ancien rapport sans contrat : ignore, pas de bruit
            checked += 1
            for v in validate_report(rdata):
                warn(f"rapport {name} : {v}")
                bad += 1
            if name == "plan.txt" and "scope" in rdata:
                for v in validate_task(rdata):
                    warn(f"rapport {name} : {v}")
                    bad += 1
        if checked and not bad:
            ok(f"rapports conformes ({checked})")

    # 8b. artefact partage fullstack.txt (Lot 3)
    if HAS_FULLSTACK_CHECK:
        p = opencode / "resume" / "fullstack.txt"
        if p.is_file():
            try:
                headers, sections = read_fullstack(p)
                for v in validate_fullstack(headers, sections):
                    warn(f"rapport fullstack.txt : {v}")
            except OSError as exc:
                warn(f"rapport fullstack.txt illisible: {exc}")
        else:
            pass  # pleinement optionnel, uniquement pour scope fullstack

    # 10. état (Lot 3 : informatif seulement, jamais bloquant)
    if HAS_STATE_CHECK:
        if not (opencode / "state").is_dir():
            warn("dossier .opencode/state absent (lance `update sayrhazi` pour le créer)")
        else:
            try:
                current = load_state(root)
            except (ValueError, RuntimeError) as exc:
                current = None
                warn(f"état illisible : {exc}")
            if current is None:
                ok("aucun état (lance `python engine/state.py --reconcile --project .`)")
            else:
                violations = validate_state(current)
                for v in violations:
                    warn(f"état : {v}")
                if not violations:
                    ok(f"état tâche {current['workflow']['task_id']} : {current['workflow']['status']}")

    # 8c. etat d'archivage fullstack (Lot 6 : informatif seulement)
    if HAS_ARCHIVE_CHECK:
        archived = archive_entries(root)
        if archived:
            ok(f"archives fullstack : {', '.join(archived)}")
        else:
            ok("aucune archive fullstack (attendue pour scope fullstack cloture)")

    # 9. workflow (contrat Lot 2 : informatif seulement, jamais bloquant)
    if HAS_WORKFLOW_CHECK:
        wf = opencode / "workflow.yaml"
        if not wf.is_file():
            warn("workflow.yaml absent (lance `update sayrhazi` pour le créer)")
        else:
            try:
                wdata, werr = parse_workflow(wf.read_text(encoding="utf-8", errors="replace"))
            except OSError as exc:
                wdata, werr = None, f"illisible: {exc}"
            if werr:
                warn(f"workflow.yaml illisible : {werr}")
            else:
                violations = validate_workflow(wdata)
                for v in violations:
                    warn(f"workflow.yaml : {v}")
                if not violations:
                    stages = wdata.get("stages", [])
                    ok(f"workflow.yaml conforme ({len(stages)} étapes)")

    # 11. agents requis (Lot 5 : informatif seulement, jamais bloquant)
    if HAS_WORKFLOW_CHECK:
        wf = opencode / "workflow.yaml"
        if wf.is_file():
            try:
                wdata, werr = parse_workflow(wf.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                wdata, werr = None, "illisible"
            if not werr and wdata:
                conditions: dict = {}
                try:
                    cfg_text = config.read_text(encoding="utf-8", errors="replace")
                except (OSError, NameError):
                    cfg_text = ""
                for condition, key in (("security_required", "security_audit_required_by_default"),
                                       ("visual_qa_required", "visual_qa_required"),
                                       ("design_required", "design_required")):
                    m = re.search(r"^\s*" + re.escape(key) + r"\s*:\s*(\S+)", cfg_text,
                                  re.MULTILINE | re.IGNORECASE)
                    conditions[condition] = bool(m) and m.group(1).strip().lower() == "true"
                for sid, agent, state in summarize_agents(wdata, conditions):
                    print(f"  {sid} -> {agent} : {state}")

    # 7. version installee vs noyau (invitation seulement, jamais bloquant)
    installed = _read_workflow_version(config) if config.is_file() else None
    latest = _read_workflow_version(_core_template())
    if latest is None:
        pass
    elif installed is None:
        warn("version installee illisible : lance `update sayrhazi` pour resynchroniser")
    elif installed == latest:
        ok(f"version a jour ({installed})")
    else:
        warn(f"version installee {installed} < noyau {latest} : lance `update sayrhazi`")

    if problems:
        print(f"Tunda sayrhazi: INVALIDE ({len(problems)} probleme(s), {len(warns)} alerte(s))")
        return 1
    if warns:
        print(f"Tunda sayrhazi: VALIDE avec {len(warns)} alerte(s)")
    else:
        print("Tunda sayrhazi: VALIDE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
