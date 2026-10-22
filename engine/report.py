#!/usr/bin/env python3
"""Contrats Sayrhazi Lot 1 — parsing tolerant et validation des rapports/taches.

Reference normative : core/schemas/report.schema.yaml et task.schema.yaml.
Stdlib uniquement. Utilise par `tunda`/`checker` (section rapports, non bloquante)
et par le watcher (Lot 4, points 3/4/5/7 du contrat).

Points de validation avant transition (docs/reports.md) :
  1. fichier attendu existe, 2. stable apres ecriture -> runtime (watcher Lot 4) ;
  3. task_id = tache active, 4. statut autorise, 5. auteur = role attendu -> ici ;
  6. pas d'execution identique en cours, 8. delai max -> runtime (watcher Lot 4) ;
  7. pas de rapport de sortie equivalent -> ici via `output_text`.
"""

from __future__ import annotations

import re
from datetime import datetime

ROLES = ("architect", "build-front", "build-back", "reviewer", "security", "qa", "frontend", "backend")

TASK_STATUSES = ("À IMPLÉMENTER", "INFORMATIF", "EN COURS", "TERMINÉ", "BLOQUÉ", "VALIDÉ")

ROLE_STATUSES: dict[str, tuple[str, ...]] = {
    # plan.txt (Lawibrahim)
    "architect": ("À IMPLÉMENTER", "INFORMATIF", "VALIDÉ"),
    # design.txt (Ali)
    "build-front": ("PROPOSÉ", "VALIDÉ"),
    # build.txt (Bamse)
    "build-back": ("EN COURS", "TERMINÉ", "BLOQUÉ"),
    # review.txt (Hadji), security.txt (Hifadhui)
    "reviewer": ("VALIDÉ", "VALIDÉ AVEC RÉSERVES", "CORRECTIONS NÉCESSAIRES"),
    "security": ("VALIDÉ", "VALIDÉ AVEC RÉSERVES", "CORRECTIONS NÉCESSAIRES"),
    # qa.txt (Zawadi)
    "qa": ("VALIDÉ", "VALIDÉ AVEC RÉSERVES", "CORRECTIONS NÉCESSAIRES", "À COMPLÉTER"),
    # frontend.txt (Ali)
    "frontend": ("EN COURS", "TERMINÉ", "BLOQUÉ"),
    # backend.txt (Bamse)
    "backend": ("EN COURS", "TERMINÉ", "BLOQUÉ"),
}

REQUIRED_REPORT_FIELDS = ("task_id", "agent", "status", "completed_at", "summary")
REQUIRED_TASK_FIELDS = ("task_id", "title", "status")

TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,}$")
KEY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")
ITEM_RE = re.compile(r"^\s*-\s+(.*)$")

# Indicateur de tache -> role exige dans agents_required (Lot 1, coherence).
INDICATOR_ROLES = (
    ("security_required", "security"),
    ("visual_qa_required", "qa"),
    ("design_required", "build-front"),
)

# Scope de tache -> roles attendus dans agents_required (Lot frontend/backend/fullstack).
SCOPE_VALUES = ("frontend", "backend", "fullstack")
SCOPE_REQUIRED_ROLES = {
    "frontend": ("build-front",),
    "backend": ("build-back",),
    "fullstack": ("build-front", "build-back"),
}

# Rôles systeme canoniques et leurs alias pour retrocompatibilite lecture.
# Les anciens rapports contenant agent: builder-front ou agent: builder-back restent lisibles.
ROLE_ALIASES: dict[str, str] = {
    "builder-front": "build-front",
    "builder-back": "build-back",
}


def normalize_role(value: object) -> str:
    """Normalise un role : remplace les anciens aliases par le canonique."""
    text = str(value or "").strip().lower()
    return ROLE_ALIASES.get(text, text)

# Etats de controle (Lot 4) et noms de controles connus.
CONTROL_STATES = ("OK", "KO", "Non requis", "En attente")
CONTROL_NAMES = ("review", "security", "visual_qa", "integrated")


def normalize_status(value: object) -> str:
    """`VALIDÉ_AVEC_RÉSERVES` -> `VALIDÉ AVEC RÉSERVES` (casse/espaces normalises)."""
    text = str(value).strip().upper().replace("_", " ")
    return re.sub(r"\s+", " ", text)


def _clean(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1]
    return text.strip()


def _flow_list(value: str) -> list[str] | None:
    text = value.strip()
    if not (text.startswith("[") and text.endswith("]")):
        return None
    inner = text[1:-1].strip()
    if not inner:
        return []
    return [_coerce(_clean(p)) for p in inner.split(",")]


def _coerce(value: str):
    """`true`/`false` -> bool, `null`/`~` -> None, sinon la chaine telle quelle."""
    low = value.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~"):
        return None
    return value


def parse_report(text: str) -> dict:
    """Parse tolerant d'un rapport/tache : lignes `cle: valeur`, listes `- ` ou `[a, b]`.

    Titres `#`, lignes libres et puces hors cle ignores. Premiere occurrence gagne
    (le corps du rapport peut rementionner un champ). Cles en minuscules.
    Le BOM UTF-8 (éditeurs Windows) est ignoré.
    """
    text = text.lstrip("\ufeff")
    data: dict = {}
    pending_key: str | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            pending_key = None
            continue
        item = ITEM_RE.match(raw)
        if item and pending_key and pending_key not in data:
            data[pending_key].append(_coerce(_clean(item.group(1))))
            continue
        match = KEY_RE.match(raw)
        pending_key = None
        if not match:
            continue
        key = match.group(1).lower()
        if key in data:
            continue
        value = match.group(2).strip()
        if value == "":
            data[key] = []
            pending_key = key
            continue
        flow = _flow_list(value)
        data[key] = flow if flow is not None else _coerce(_clean(value))
    return data


def _parse_moment(value: object) -> bool:
    try:
        datetime.fromisoformat(str(value).strip().strip("\"'").replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def validate_report(data: dict) -> list[str]:
    """Valide un rapport contre le contrat Lot 1. Retourne les violations."""
    errors: list[str] = []
    for field in REQUIRED_REPORT_FIELDS:
        if field not in data or data[field] in ("", [], None):
            errors.append(f"champ requis manquant `{field}`")
    task_id = data.get("task_id")
    if task_id and not TASK_ID_RE.match(str(task_id)):
        errors.append(f"task_id `{task_id}` instable (lettres/chiffres/._-, min 3 caracteres)")
    agent_raw = str(data.get("agent", "")).strip().lower()
    agent = normalize_role(agent_raw)
    if agent and agent not in ROLES:
        errors.append(f"agent `{data.get('agent')}` inconnu (attendu : {list(ROLES)})")
    status = data.get("status")
    if status and agent in ROLES and normalize_status(status) not in ROLE_STATUSES[agent]:
        errors.append(
            f"status `{status}` non autorise pour {agent} "
            f"(attendu : {list(ROLE_STATUSES[agent])})"
        )
    completed = data.get("completed_at")
    if completed and not _parse_moment(completed):
        errors.append(f"completed_at `{completed}` non ISO 8601")
    version = data.get("version")
    if version and _parse_moment(version):
        errors.append("champ `version` horodatage refuse, utiliser `completed_at`")
    next_agents = data.get("next_agents")
    if next_agents:
        agents = next_agents if isinstance(next_agents, list) else [next_agents]
        for name in agents:
            if normalize_role(name) not in ROLES:
                errors.append(f"next_agents `{name}` inconnu (attendu : {list(ROLES)})")
    return errors


def validate_task(data: dict) -> list[str]:
    """Valide une tache contre le contrat Lot 1 (champs + coherence indicateurs)."""
    errors: list[str] = []
    for field in REQUIRED_TASK_FIELDS:
        if field not in data or data[field] in ("", [], None):
            errors.append(f"champ requis manquant `{field}`")
    task_id = data.get("task_id")
    if task_id and not TASK_ID_RE.match(str(task_id)):
        errors.append(f"task_id `{task_id}` instable (lettres/chiffres/._-, min 3 caracteres)")
    status = data.get("status")
    if status and normalize_status(status) not in TASK_STATUSES:
        errors.append(f"status `{status}` non autorise (attendu : {list(TASK_STATUSES)})")
    required = data.get("agents_required")
    if required is not None:
        agents = required if isinstance(required, list) else [required]
        agents_norm = [normalize_role(a) for a in agents]
        for name in agents:
            if normalize_role(name) not in ROLES:
                errors.append(f"agents_required `{name}` inconnu (attendu : {list(ROLES)})")
        for indicator, role in INDICATOR_ROLES:
            if data.get(indicator) is True and role not in agents_norm:
                errors.append(f"{indicator}: true exige `{role}` dans agents_required")
    scope = data.get("scope")
    if scope is not None:
        if str(scope).strip() not in SCOPE_VALUES:
            errors.append(f"scope `{scope}` invalide (attendu : {list(SCOPE_VALUES)})")
        else:
            required = SCOPE_REQUIRED_ROLES.get(str(scope).strip(), ())
            for role in required:
                if role not in agents_norm:
                    errors.append(f"scope `{scope}` exige `{role}` dans agents_required")
    controls = data.get("controls")
    if controls is not None:
        if not isinstance(controls, dict):
            errors.append("controls doit etre un mapping nom_controle -> etat")
        else:
            for name, etat in controls.items():
                if str(etat).strip() not in CONTROL_STATES:
                    errors.append(f"controle {name}: etat `{etat}` invalide (attendu : {list(CONTROL_STATES)})")
    controls_required = data.get("controls_required")
    if controls_required is not None:
        required_list = controls_required if isinstance(controls_required, list) else [controls_required]
        for name in required_list:
            name = str(name).strip()
            if name not in CONTROL_NAMES:
                errors.append(f"controle requis `{name}` inconnu (attendu : {list(CONTROL_NAMES)})")
            else:
                etat = (controls or {}).get(name)
                if etat is None or str(etat).strip() == "En attente":
                    errors.append(f"controle requis `{name}` est en attente (n'est pas encore OK)")
                elif str(etat).strip() == "Non requis":
                    errors.append(f"controle requis `{name}` ne peut pas etre marque 'Non requis'")
    return errors


def check_transition(
    data: dict,
    *,
    expected_agent: str,
    expected_task_id: str,
    allowed_statuses: tuple[str, ...] | None = None,
    output_text: str | None = None,
) -> list[str]:
    """Points 3/4/5/7 du contrat avant transition. Retourne les violations."""
    errors: list[str] = []
    if str(data.get("task_id", "")).strip() != expected_task_id:
        errors.append(f"task_id `{data.get('task_id')}` != tache active {expected_task_id} (point 3)")
    if str(data.get("agent", "")).strip().lower() != expected_agent:
        errors.append(f"agent `{data.get('agent')}` != role attendu {expected_agent} (point 5)")
    allowed = allowed_statuses or ROLE_STATUSES.get(expected_agent, ())
    if normalize_status(data.get("status", "")) not in [normalize_status(s) for s in allowed]:
        errors.append(f"status `{data.get('status')}` non autorise pour cette transition (point 4)")
    if output_text is not None and expected_task_id not in output_text:
        errors.append("rapport de sortie absent ou sans le task_id actif (point 7)")
    return errors
