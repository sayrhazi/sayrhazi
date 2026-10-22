#!/usr/bin/env python3
"""State Sayrhazi — lecture/ecriture de .opencode/state/workflow-state.yaml.

Fondation (lots automation) : vue de coordination {task_id, status,
current/completed/pending/blocked, running}. Les rapports restent les preuves.
Stdlib uniquement.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from sayrhazi_config import parse_simple_yaml
except ImportError:  # pragma: no cover
    parse_simple_yaml = None  # type: ignore
try:
    from report import normalize_status, normalize_role, parse_report, validate_report
    HAS_REPORTS = True
except ImportError:  # pragma: no cover
    HAS_REPORTS = False

    def normalize_status(value) -> str:  # type: ignore
        return str(value).strip().upper().replace("_", " ")

    def normalize_role(value) -> str:  # type: ignore
        return str(value).strip().lower()
try:
    from workflow import parse_workflow
    HAS_WORKFLOW = True
except ImportError:  # pragma: no cover
    HAS_WORKFLOW = False

STATUSES = ("PENDING", "READY", "RUNNING", "IN_PROGRESS", "TERMINÉ",
            "VALIDÉ", "VALIDÉ_AVEC_RÉSERVES", "CORRECTIONS_NÉCESSAIRES",
            "BLOCKED", "FAILED", "CANCELLED")

# Étape -> rapport qui prouve son achèvement (Lot 3, réconciliation).
STAGE_REPORTS = {"planning": "plan.txt", "design": "frontend.txt",
                 "implementation": "backend.txt", "review": "review.txt",
                 "security": "security.txt", "visual_qa": "qa.txt"}

# Rapport déclencheur par défaut (surchargé par workflow.yaml si présent).
DEFAULT_TRIGGERS = {"planning": None, "design": "plan.txt",
                    "implementation": "plan.txt", "review": "backend.txt",
                    "security": "backend.txt", "visual_qa": "backend.txt"}

# Statuts qui achèvent l'étape (le reste = non terminé ; CORRECTIONS et
# qa.txt À COMPLÉTER rouvrent build-back).
FINAL_STATUSES = {"architect": ("VALIDÉ",), "build-front": ("VALIDÉ",),
                  "build-back": ("TERMINÉ",),
                  "reviewer": ("VALIDÉ", "VALIDÉ AVEC RÉSERVES"),
                  "security": ("VALIDÉ", "VALIDÉ AVEC RÉSERVES"),
                  "qa": ("VALIDÉ", "VALIDÉ AVEC RÉSERVES")}

REPORT_ROLES = {"plan.txt": "architect", "frontend.txt": "build-front",
                "backend.txt": "build-back", "review.txt": "reviewer",
                "security.txt": "security", "qa.txt": "qa"}


def default_state(task_id: str) -> dict:
    return {"workflow": {"task_id": task_id, "status": "PENDING",
                         "started_at": None,
                         "current_stages": [], "completed_stages": [],
                         "pending_stages": [], "blocked_stages": [],
                         "running_agents": []}}


def _scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    s = str(value)
    if s == "" or any(c in s for c in ":#{}[],&*?|-<>!=@`\"'"):
        return '"' + s.replace('"', '\\"') + '"'
    return s


def _emit(value, indent: int = 0) -> list[str]:
    pad = "  " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for k, v in value.items():
            if isinstance(v, dict):
                lines.append(pad + str(k) + ":")
                lines.extend(_emit(v, indent + 1))
            elif isinstance(v, list):
                # Listes inline (flow) : le parseur ne supporte que ce format.
                lines.append(pad + str(k) + ": [" + ", ".join(_scalar(x) for x in v) + "]")
            else:
                lines.append(pad + str(k) + ": " + _scalar(v))
        return lines
    if isinstance(value, list):
        # Style flow ([a, b]) : seul format de liste supporte par le parseur.
        return [pad + "[" + ", ".join(_scalar(v) for v in value) + "]"]
    return [pad + _scalar(value)]


def state_path(project_root: Path | str) -> Path:
    return Path(project_root) / ".opencode" / "state" / "workflow-state.yaml"


def save_state(project_root: Path | str, state: dict) -> Path:
    path = state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(_emit(state)) + "\n", encoding="utf-8")
    return path


def load_state(project_root: Path | str) -> dict | None:
    if parse_simple_yaml is None:
        raise RuntimeError("parseur indisponible")
    path = state_path(project_root)
    if not path.is_file():
        return None
    data, err = parse_simple_yaml(path.read_text(encoding="utf-8", errors="replace"))
    if err:
        raise ValueError("etat illisible : " + err)
    return data


def validate_state(state: dict) -> list[str]:
    """Valide un état chargé. Retourne les violations (jamais bloquant)."""
    errors: list[str] = []
    wf = state.get("workflow") if isinstance(state, dict) else None
    if not isinstance(wf, dict):
        return ["workflow: mapping requis"]
    if not wf.get("task_id"):
        errors.append("workflow.task_id absent")
    if wf.get("status") not in STATUSES:
        errors.append(f"workflow.status `{wf.get('status')}` inconnu (attendu : {list(STATUSES)})")
    for key in ("current_stages", "completed_stages", "pending_stages",
                "blocked_stages", "running_agents"):
        if not isinstance(wf.get(key), list):
            errors.append(f"workflow.{key} doit être une liste")
    # `session:` appartenait au suivi de sessions du watcher, supprimé en
    # 0.13.0. Un état plus ancien peut encore le contenir : accepté, jamais
    # exigé, jamais réécrit (les métriques d'appel sont lues chez OpenCode).
    return errors


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def reconcile(project_root: Path | str, task_id: str | None = None) -> tuple[dict | None, list[str]]:
    """Réconcilie état + config + rapports (Lot 3, reprise au redémarrage).

    1. lit l'état persistant ; 2. relit la config ; 3. vérifie les rapports du
    `task_id` actif ; 4. réconcilie rapports et étapes ; 5. ne retient que les
    étapes éligibles ; 6. ne relance jamais une étape terminée (sauf retour
    `CORRECTIONS NÉCESSAIRES` vers l'implémentation) ; 7. signale toute
    incohérence au lieu de l'ignorer. Ne sauvegarde pas : à l'appelant
    d'appeler save_state(). Retourne (etat, notes).
    """
    root = Path(project_root)
    notes: list[str] = []
    try:
        state = load_state(root)
    except (ValueError, RuntimeError) as exc:
        notes.append(f"état illisible, reconstruit : {exc}")
        state = None
    active = (state.get("workflow", {}).get("task_id") if state else None) or task_id

    reports: dict = {}
    if not HAS_REPORTS:
        notes.append("validateur de rapports indisponible : réconciliation limitée")
    else:
        resume = root / ".opencode" / "resume"
        if resume.is_dir():
            for name in STAGE_REPORTS.values():
                path = resume / name
                if not path.is_file():
                    continue
                try:
                    data = parse_report(path.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
                if "task_id" not in data:
                    notes.append(f"rapport {name} sans task_id ignoré")
                    continue
                reports[name] = data

    if active is None:
        tids = sorted({d["task_id"] for d in reports.values()})
        if len(tids) == 1:
            active = tids[0]
            notes.append(f"tâche active déduite des rapports : {active}")
        else:
            if tids:
                notes.append(f"tâches multiples {tids} : préciser task_id, rien à réconcilier")
            else:
                notes.append("aucun état ni rapport : rien à réconcilier")
            return None, notes

    mine = {n: d for n, d in reports.items() if d.get("task_id") == active}
    ignored = sorted(set(reports) - set(mine))
    if ignored:
        notes.append(f"rapports d'une autre tâche ignorés : {ignored}")
    if state is None:
        state = default_state(active)
    else:
        state["workflow"]["task_id"] = active
    wf = state["workflow"]

    valid: dict = {}
    for name, data in mine.items():
        problems = validate_report(data) if HAS_REPORTS else []
        if problems:
            notes.append(f"rapport {name} ignoré ({problems[0]})")
        else:
            valid[name] = data

    stages = list(STAGE_REPORTS)
    triggers = dict(DEFAULT_TRIGGERS)
    if HAS_WORKFLOW:
        config = root / ".opencode" / "workflow.yaml"
        if config.is_file():
            try:
                wdata, werr = parse_workflow(config.read_text(encoding="utf-8", errors="replace"))
            except OSError as exc:
                wdata, werr = None, str(exc)
            if werr:
                notes.append(f"workflow.yaml illisible : {werr}")
            elif isinstance(wdata, dict) and isinstance(wdata.get("stages"), list):
                stages = [s.get("id") for s in wdata["stages"]
                          if isinstance(s, dict) and s.get("id")]
                for s in wdata["stages"]:
                    if isinstance(s, dict) and s.get("id"):
                        trig = (s.get("trigger") or {}).get("report")
                        if trig:
                            triggers[s["id"]] = trig
        else:
            notes.append("workflow.yaml absent : étapes par défaut")

    completed: list[str] = []
    pending: list[str] = []
    blocked: list[str] = []
    build_ok = "build.txt" in valid or "backend.txt" in valid
    for sid in stages:
        rep = STAGE_REPORTS.get(sid)
        data = valid.get(rep) if rep else None
        if data is None:
            pending.append(sid)
            continue
        status = normalize_status(data.get("status", ""))
        role = REPORT_ROLES.get(rep or "")
        if sid in ("review", "security", "visual_qa") and not build_ok:
            blocked.append(sid)
            notes.append(f"{sid} bloquée : {rep} sans build.txt valide")
            continue
        if status == "BLOQUÉ":
            blocked.append(sid)
            notes.append(f"{sid} bloquée par le rapport {rep}")
        elif status in FINAL_STATUSES.get(role or "", ()):
            completed.append(sid)
        elif status == "CORRECTIONS NÉCESSAIRES" and sid in ("review", "security", "visual_qa"):
            completed.append(sid)
        else:
            pending.append(sid)
    # Un verdict CORRECTIONS rouvre l'implémentation (retour vers Bamse).
    for sid in ("review", "security", "visual_qa"):
        rep = STAGE_REPORTS.get(sid)
        data = valid.get(rep) if rep else None
        if data is not None and normalize_status(data.get("status", "")) == "CORRECTIONS NÉCESSAIRES":
            if "implementation" in completed:
                completed.remove("implementation")
            if "implementation" not in pending:
                pending.append("implementation")
            notes.append(f"{rep} demande des corrections : implementation à reprendre")
    # Un qa.txt À COMPLÉTER rouvre aussi l'implémentation (Zawadi attend
    # une action de Bamse : purge cache, relance frontal, fix env).
    qa_data = valid.get("qa.txt")
    if qa_data is not None and normalize_status(qa_data.get("status", "")) == "À COMPLÉTER":
        if "implementation" in completed:
            completed.remove("implementation")
        if "implementation" not in pending:
            pending.append("implementation")
        notes.append("qa.txt à compléter : implementation à reprendre")

    current = [s for s in pending if triggers.get(s) is None or triggers.get(s) in valid]
    waiting = [s for s in pending if s not in current]
    wf["completed_stages"] = completed
    wf["pending_stages"] = waiting
    wf["current_stages"] = current
    wf["blocked_stages"] = blocked
    wf["running_agents"] = []
    if not wf.get("started_at"):
        wf["started_at"] = _now_iso()
    wf["status"] = "TERMINÉ" if not pending and not blocked and completed else "IN_PROGRESS"
    return state, notes


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="État Sayrhazi : afficher ou réconcilier")
    parser.add_argument("--project", default=".", help="racine du projet")
    parser.add_argument("--reconcile", action="store_true", help="réconcilier et sauvegarder")
    parser.add_argument("--task", default=None, help="task_id actif (si aucun état)")
    args = parser.parse_args()

    if args.reconcile:
        state, notes = reconcile(args.project, args.task)
        for note in notes:
            print("- " + note)
        if state is None:
            print("Rien à réconcilier.")
            return 1
        path = save_state(args.project, state)
        print(f"État réconcilié : {path}")
        print(f"Tâche {state['workflow']['task_id']} : {state['workflow']['status']}, "
              f"terminées {state['workflow']['completed_stages']}, "
              f"prêtes {state['workflow']['current_stages']}, "
              f"en attente {state['workflow']['pending_stages']}, "
              f"bloquées {state['workflow']['blocked_stages']}.")
        return 0

    try:
        state = load_state(args.project)
    except (ValueError, RuntimeError) as exc:
        print(f"État illisible : {exc}")
        return 1
    if state is None:
        print("Aucun état (lance avec --reconcile).")
        return 1
    for violation in validate_state(state):
        print(f"- [état] {violation}")
    print(f"Tâche {state['workflow']['task_id']} : {state['workflow']['status']}.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
