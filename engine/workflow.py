#!/usr/bin/env python3
"""Workflow Sayrhazi Lot 2 — parsing tolerant et validation de workflow.yaml.

Reference normative : core/workflow/workflow.yaml. Contrat projet :
`.opencode/workflow.yaml` (modele : runtimes/opencode/templates/workflow.yaml).
Stdlib uniquement. Les statuts et roles viennent du Lot 1 (engine/report.py) :
un trigger n'est valide que si son statut est autorise pour le role qui
produit le rapport declencheur (ex. plan.txt + VALIDÉ via architect).

Le mini-parseur supporte mappings imbriques (2 espaces), listes `- ` et
listes flow `[a, b]`, contrairement a sayrhazi_config (mappings seuls).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from report import ROLES, ROLE_STATUSES, normalize_status, normalize_role
except ImportError:  # pragma: no cover
    ROLES = ("architect", "build-front", "build-back", "reviewer", "security", "qa")
    ROLE_STATUSES = {}
    normalize_status = str
    normalize_role = str

# Rapport -> role qui le produit (le trigger doit porter un statut de ce role).
REPORT_PRODUCERS = {
    "plan.txt": "architect",
    "frontend.txt": "build-front",
    "backend.txt": "build-back",
    "review.txt": "reviewer",
    "security.txt": "security",
    "qa.txt": "qa",
}

KNOWN_CONDITIONS = ("security_required", "visual_qa_required", "design_required")


class WorkflowError(ValueError):
    """Erreur de structure avec numero de ligne."""


def _strip_comment(line: str) -> str:
    out: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "#" and out and out[-1] in (" ", "\t"):
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out).rstrip()


def _coerce(value: str):
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    low = text.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~"):
        return None
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [] if not inner else [_coerce(p) for p in inner.split(",")]
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _parse_block(items: list, pos: int, indent: int) -> tuple[dict, int]:
    data: dict = {}
    while pos < len(items):
        lineno, ind, content = items[pos]
        if ind < indent:
            break
        if ind > indent:
            raise WorkflowError(f"ligne {lineno} : indentation inattendue `{content}`")
        if content.startswith("- "):
            raise WorkflowError(f"ligne {lineno} : liste `- ` inattendue (clef attendue)")
        if ":" not in content:
            raise WorkflowError(f"ligne {lineno} : attendu `cle: valeur`")
        key, _, value = content.partition(":")
        key = key.strip()
        value = value.strip()
        if not key or " " in key:
            raise WorkflowError(f"ligne {lineno} : cle invalide `{key}`")
        if key in data:
            raise WorkflowError(f"ligne {lineno} : cle dupliquee `{key}`")
        if value == "":
            if pos + 1 < len(items) and items[pos + 1][1] > indent:
                nxt = items[pos + 1][2]
                if nxt.startswith("- "):
                    child, pos = _parse_list(items, pos + 1, items[pos + 1][1])
                else:
                    child, pos = _parse_block(items, pos + 1, items[pos + 1][1])
                data[key] = child
            else:
                data[key] = None
                pos += 1
        else:
            data[key] = _coerce(value)
            pos += 1
    return data, pos


def _parse_list(items: list, pos: int, indent: int) -> tuple[list, int]:
    out: list = []
    while pos < len(items):
        lineno, ind, content = items[pos]
        if ind < indent or not content.startswith("- "):
            break
        if ind > indent:
            raise WorkflowError(f"ligne {lineno} : indentation inattendue `{content}`")
        rest = content[2:].strip()
        if rest == "":
            if pos + 1 < len(items) and items[pos + 1][1] > indent:
                child, pos = _parse_block(items, pos + 1, items[pos + 1][1])
                out.append(child)
            else:
                out.append(None)
                pos += 1
            continue
        if ":" in rest and not rest.startswith(("[", "'", '"')):
            key, _, value = rest.partition(":")
            key = key.strip()
            value = value.strip()
            if not key or " " in key:
                out.append(_coerce(rest))
                pos += 1
                continue
            item: dict = {}
            if value == "":
                if pos + 1 < len(items) and items[pos + 1][1] > indent:
                    if items[pos + 1][2].startswith("- "):
                        child, pos = _parse_list(items, pos + 1, items[pos + 1][1])
                    else:
                        child, pos = _parse_block(items, pos + 1, items[pos + 1][1])
                    item[key] = child
                else:
                    item[key] = None
                    pos += 1
            else:
                item[key] = _coerce(value)
                pos += 1
            if pos < len(items) and items[pos][1] > indent and not items[pos][2].startswith("- "):
                sub, pos = _parse_block(items, pos, items[pos][1])
                for k, v in sub.items():
                    if k in item:
                        raise WorkflowError(f"ligne {items[pos - 1][0]} : cle dupliquee `{k}`")
                    item[k] = v
            out.append(item)
        else:
            out.append(_coerce(rest))
            pos += 1
    return out, pos


def parse_workflow(text: str) -> tuple[dict | None, str | None]:
    """Retourne (donnees, None) ou (None, message_erreur)."""
    text = text.lstrip("\ufeff")
    items: list = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        head = raw[: len(raw) - len(raw.lstrip())]
        if "\t" in head:
            return None, f"ligne {lineno} : tabulations interdites (espaces uniquement)"
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        if indent % 2:
            return None, f"ligne {lineno} : indentation impaire ({indent} espaces)"
        items.append([lineno, indent, stripped.strip()])
    if not items:
        return {}, None
    try:
        data, pos = _parse_block(items, 0, 0)
    except WorkflowError as exc:
        return None, str(exc)
    if pos != len(items):
        return None, f"ligne {items[pos][0]} : contenu inattendu `{items[pos][2]}`"
    return data, None


def validate_workflow(data: dict) -> list[str]:
    """Valide un workflow parse. Retourne les violations."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["racine : mapping attendu"]
    for key in data:
        if key not in ("workflow", "stages"):
            errors.append(f"cle inattendue `{key}` (attendu : workflow, stages)")
    head = data.get("workflow")
    if not isinstance(head, dict):
        errors.append("workflow: mapping requis (mode, auto_start, max_parallel_agents)")
    else:
        for key in head:
            if key not in ("mode", "auto_start", "max_parallel_agents"):
                errors.append(f"workflow : cle inattendue `{key}`")
        if head.get("mode") != "supervised":
            errors.append(f"workflow.mode doit valoir `supervised` (reçu : {head.get('mode')!r})")
        if head.get("auto_start") is not False:
            errors.append("workflow.auto_start doit être false (mode supervisé initial)")
        mpa = head.get("max_parallel_agents")
        if not isinstance(mpa, int) or isinstance(mpa, bool) or mpa < 1:
            errors.append(f"workflow.max_parallel_agents doit être un entier >= 1 (reçu : {mpa!r})")
    stages = data.get("stages")
    if not isinstance(stages, list) or not stages:
        errors.append("stages: liste non vide requise")
        return errors
    seen: set = set()
    for i, stage in enumerate(stages):
        where = f"stages[{i}]"
        if not isinstance(stage, dict):
            errors.append(f"{where} : mapping attendu")
            continue
        sid = stage.get("id")
        if not isinstance(sid, str) or not sid:
            errors.append(f"{where} : id requis (chaîne non vide)")
        elif sid in seen:
            errors.append(f"{where} : id dupliqué `{sid}`")
        else:
            seen.add(sid)
        agent = stage.get("agent")
        if agent not in ROLES:
            errors.append(f"{where} : agent `{agent}` inconnu (attendu : {list(ROLES)})")
        condition = stage.get("condition")
        if condition is not None and condition not in KNOWN_CONDITIONS:
            errors.append(f"{where} : condition `{condition}` inconnue (attendu : {list(KNOWN_CONDITIONS)})")
        trigger = stage.get("trigger")
        if not isinstance(trigger, dict):
            errors.append(f"{where} : trigger requis (report + status)")
        else:
            for key in trigger:
                if key not in ("report", "status"):
                    errors.append(f"{where} : trigger : cle inattendue `{key}`")
            report = trigger.get("report")
            status = trigger.get("status")
            if report not in REPORT_PRODUCERS:
                errors.append(f"{where} : trigger.report `{report}` inconnu (attendu : {list(REPORT_PRODUCERS)})")
            if not isinstance(status, str) or not status:
                errors.append(f"{where} : trigger.status requis")
            elif report in REPORT_PRODUCERS and ROLE_STATUSES:
                producer = REPORT_PRODUCERS[report]
                if normalize_status(status) not in ROLE_STATUSES.get(producer, ()):
                    errors.append(
                        f"{where} : status `{status}` non autorisé pour {report} "
                        f"(rôle {producer} : {list(ROLE_STATUSES.get(producer, ()))})"
                    )
        auto = stage.get("auto", False)
        if not isinstance(auto, bool):
            errors.append(f"{where} : auto doit être booléen (reçu : {auto!r})")
        for key in stage:
            if key not in ("id", "agent", "condition", "trigger", "auto"):
                errors.append(f"{where} : cle inattendue `{key}`")
    return errors


def summarize_agents(workflow_data: dict, conditions: dict) -> list:
    """Agents requis par la config : [(id étape, agent, état), ...].

    état : `requis` (auto sans condition ou condition vraie), `manuel`
    (auto: false), `inactif (condition à false)` (auto + condition fausse).
    """
    rows: list = []
    stages = workflow_data.get("stages") if isinstance(workflow_data, dict) else []
    if not isinstance(stages, list):
        return rows
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        sid = stage.get("id", "?")
        agent = stage.get("agent", "?")
        if not stage.get("auto", False):
            rows.append((sid, agent, "manuel"))
        elif not stage.get("condition"):
            rows.append((sid, agent, "requis"))
        elif conditions.get(stage["condition"], False):
            rows.append((sid, agent, "requis"))
        else:
            rows.append((sid, agent, f"inactif ({stage['condition']} à false)"))
    return rows
