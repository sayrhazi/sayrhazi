"""
sayrhazi-modules — briques communes Sayrhazi (interne).

Ne pas lancer directement. Ce module agrège les fonctions de lecture
(sans effet de bord) utilisées par le dashboard web et les tests.

Aucune dépendance externe, aucune dépendance à textual. Windows / Linux.
Les chemins sont résolus depuis l'emplacement du fichier (comme pour
sayrhazi-serveur.py l'emplacement de ce module).
"""

import html
import json
import os
import re
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

STATIC_DIR = _here / "static"

DEFAULT_PORT = 8363

ROLE_ORDER = ("architect", "build-front", "build-back", "reviewer", "security", "qa")
ROLE_AGENT_FILE = {"architect": "lawibrahim", "build-front": "ali",
                   "build-back": "bamse", "reviewer": "hadji",
                   "security": "hifadhui", "qa": "zawadi",
                   "frontend": "ali", "backend": "bamse"}
ROLE_REPORT = {"architect": "plan.txt", "build-front": "frontend.txt",
               "build-back": "backend.txt", "reviewer": "review.txt",
               "security": "security.txt", "qa": "qa.txt",
               "frontend": "fullstack.txt", "backend": "fullstack.txt"}

TASK_RE = re.compile(r"^\s*task_id\s*:\s*(\S+)\s*$", re.MULTILINE | re.IGNORECASE)
STATUS_RE = re.compile(r"^\s*status\s*:\s*(\S.*?)\s*$", re.MULTILINE | re.IGNORECASE)
SECTION_HEADER_RE = re.compile(r"^## Étape (frontend|backend) — (Ali|Bamse)$")

STATE_KEYS = ("task_id", "status", "running_agents")


def _unquote(text: str) -> str:
    stripped = (text or "").strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in ("'", '"'):
        return stripped[1:-1]
    return stripped


def _flow_items(text: str) -> list:
    stripped = (text or "").strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        inner = stripped[1:-1].strip()
        if not inner:
            return []
        return [_unquote(p) for p in inner.split(",")]
    return [stripped] if stripped else []


def find_project_root(start: Path):
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".opencode" / "sayrhazi.yaml").is_file():
            return candidate
    try:
        script_parent = Path(__file__).resolve().parent
        if script_parent.name == ".opencode":
            return script_parent.parent
        if (script_parent.parent / ".opencode" / "sayrhazi.yaml").is_file():
            return script_parent.parent
    except NameError:
        pass
    return None


def read_state_minimal(project_root: Path) -> dict:
    path = project_root / ".opencode" / "state" / "workflow-state.yaml"
    data: dict = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return data
    for line in lines:
        match = re.match(r"^\s{2}(\w+)\s*:\s*(.*)$", line)
        if match and match.group(1) in STATE_KEYS:
            data[match.group(1)] = _unquote(match.group(2))
    return data


def parse_report(path: Path):
    try:
        text = path.read_text(encoding="utf-8", errors="replace").lstrip("﻿")
        mtime = path.stat().st_mtime
    except OSError:
        return None, None, None
    task = TASK_RE.search(text)
    status = STATUS_RE.search(text)
    return ((task.group(1).strip() if task else None),
            (status.group(1).strip().upper() if status else None), mtime)


def parse_fullstack_section(path: Path, section: str) -> tuple[Optional[str], Optional[str]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").lstrip("﻿")
    except OSError:
        return None, None
    current = None
    task_id = None
    stage_status_re = re.compile(r"^\s*stage_status\s*:\s*(\S.*?)\s*$", re.IGNORECASE)
    for raw in text.splitlines():
        m = SECTION_HEADER_RE.match(raw.strip())
        if m:
            current = m.group(1)
            continue
        if current == section:
            tm = TASK_RE.search(raw)
            if tm:
                task_id = tm.group(1).strip()
            sm = stage_status_re.search(raw)
            if sm:
                return task_id, sm.group(1).strip().upper()
    return task_id, None


def tail_lines(path: Path, count: int) -> list:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-max(1, count):] if lines else []


def tail_lines_today(path: Path) -> list:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    dates_seen = set()
    for line in lines:
        if line.strip() and len(line) >= 10:
            date_part = line[:10]
            if date_part[:4].isdigit() and date_part[4] == "-" and date_part[7] == "-":
                dates_seen.add(date_part)
    if not dates_seen:
        return []
    last_day = sorted(dates_seen)[-1]
    return [line for line in lines if line.startswith(last_day)]


def lock_info(project_root: Path):
    path = project_root / ".opencode" / "state" / "watcher.lock"
    try:
        content = path.read_text(encoding="utf-8", errors="replace").strip()
        pid = int(content.split()[0])
        age = time.time() - path.stat().st_mtime
        return pid, age
    except (OSError, ValueError, IndexError):
        return None, None


def read_plan_scope(project_root: Path) -> tuple[str | None, dict]:
    path = project_root / ".opencode" / "resume" / "plan.txt"
    try:
        text = path.read_text(encoding="utf-8", errors="replace").lstrip("﻿")
    except OSError:
        return None, {}
    scope = None
    controls: dict = {}
    scope_match = re.search(r"^\s*scope\s*:\s*(\S+)", text, re.MULTILINE | re.IGNORECASE)
    if scope_match:
        scope = scope_match.group(1).strip()
    for line in text.splitlines():
        m = re.match(r"^\s{2}(\w+)\s*:\s*(\S+)", line)
        if m and m.group(1) in ("review", "security", "visual_qa", "integrated"):
            controls[m.group(1)] = m.group(2).strip()
    return scope, controls


def read_archived_task_ids(project_root: Path) -> set:
    path = project_root / ".opencode" / "history" / "fullstack-log.md"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return set(re.findall(r"^## Tâche (\S+)$", text, re.MULTILINE))


SESSION_DB_PARTS = (".local", "share", "opencode", "opencode.db")


def opencode_db_path():
    candidate = Path(os.path.expanduser("~")).joinpath(*SESSION_DB_PARTS)
    return candidate if candidate.is_file() else None


def _num(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def load_model_limits(project_root: Path) -> dict:
    here = Path(__file__).resolve().parent
    repo_root = here.parent.parent.parent
    candidates = [here / "models.yaml",
                  repo_root / "core" / "runtime" / "models.yaml",
                  project_root / ".opencode" / "models.yaml"]
    limits: dict = {}
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        current: Optional[str] = None
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].rstrip() if not raw.strip().startswith("#") else ""
            if not line.strip():
                continue
            match = re.match(r"^\s{2}(\S+):\s*$", line)
            if match:
                current = match.group(1).strip('"').strip("'")
                continue
            match = re.match(r"^\s+context_tokens\s*:\s*(\S+)", line)
            if match and current:
                try:
                    value = int(str(match.group(1)).strip().strip('"').strip("'"))
                except (TypeError, ValueError):
                    value = 0
                if value > 0:
                    limits[current] = value
                current = None
    return limits


def find_sessions(db_path, directory: str, agent_file: str, prefix: str) -> list:
    try:
        import sqlite3
        db = sqlite3.connect("file:" + str(db_path) + "?mode=ro", uri=True, timeout=5)
        try:
            cur = db.cursor()
            cur.execute(
                "SELECT id, title, cost, time_created, time_updated FROM session"
                " WHERE directory = ? AND agent = ?"
                " ORDER BY time_updated ASC",
                ((directory or "").replace("\\", "/"), agent_file))
            rows = [dict(zip(("id", "title", "cost", "time_created", "time_updated"), row))
                    for row in cur.fetchall()]
        finally:
            db.close()
    except Exception:
        return []
    if prefix:
        for row in rows:
            row["auto"] = str(row.get("title") or "").startswith(prefix)
    else:
        for row in rows:
            row["auto"] = False
    return rows


def latest_manual_session(sessions: list) -> Optional[dict]:
    manuelles = [s for s in sessions if not s.get("auto")]
    return manuelles[-1] if manuelles else None


def latest_auto_session(sessions: list) -> Optional[dict]:
    auto = [s for s in sessions if s.get("auto")]
    return auto[-1] if auto else None


def last_assistant_message(db_path, session_id: str) -> Optional[dict]:
    try:
        import json
        import sqlite3
        db = sqlite3.connect("file:" + str(db_path) + "?mode=ro", uri=True, timeout=5)
        try:
            cur = db.cursor()
            cur.execute("SELECT data FROM message WHERE session_id = ?"
                        " ORDER BY time_created DESC", (session_id,))
            for (data,) in cur.fetchall():
                try:
                    payload = json.loads(data)
                except (ValueError, TypeError):
                    continue
                if not isinstance(payload, dict) or payload.get("role") != "assistant":
                    continue
                if not isinstance(payload.get("tokens"), dict):
                    continue
                if message_context_tokens(payload) <= 0:
                    continue
                return payload
            return None
        finally:
            db.close()
    except Exception:
        return None


def message_context_tokens(payload: dict) -> int:
    tokens = (payload or {}).get("tokens") or {}
    cache = tokens.get("cache") or {}
    return (_num(tokens.get("input")) + _num(tokens.get("output"))
            + _num(tokens.get("reasoning")) + _num(cache.get("read"))
            + _num(cache.get("write")))


def message_model_key(payload: dict) -> str:
    provider = str((payload or {}).get("providerID") or "").strip()
    model = str((payload or {}).get("modelID") or "").strip()
    if not model:
        nested = (payload or {}).get("model") or {}
        provider = provider or str(nested.get("providerID") or "").strip()
        model = str(nested.get("modelID") or "").strip()
    if not model:
        return ""
    return f"{provider}/{model}" if provider else model


def format_tokens(count) -> str:
    try:
        return f"{max(0, int(count or 0)):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "-"


def format_cost(value) -> str:
    try:
        return f"${max(0.0, float(value or 0.0)):.2f}"
    except (TypeError, ValueError):
        return "-"


def message_token_split(payload: dict) -> dict:
    tokens = (payload or {}).get("tokens") or {}
    cache = tokens.get("cache") or {}
    return {"input": _num(tokens.get("input")),
            "cache.read": _num(cache.get("read")),
            "cache.write": _num(cache.get("write")),
            "output": _num(tokens.get("output")),
            "reasoning": _num(tokens.get("reasoning"))}


def format_span(seconds: float) -> str:
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    minutes, rest = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m" if rest == 0 else f"{minutes}m{rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}j {hours:02d}h"


def format_age(mtime: float) -> str:
    if not mtime:
        return "-"
    delta = max(0, int(time.time() - mtime))
    if delta < 60:
        return f"{delta}s"
    minutes, rest = divmod(delta, 60)
    if minutes < 60:
        return f"{minutes}m" if rest == 0 else f"{minutes}m{rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def short_session(session_id: str) -> str:
    text = str(session_id or "")
    if text.startswith("ses_"):
        text = text[4:]
    return text[:10] if text else "?"


def bar(usage: Optional[float], width: int = 12) -> str:
    if usage is None:
        return "·" * width
    try:
        pct = max(0.0, min(1.0, float(usage)))
    except (TypeError, ValueError):
        return "·" * width
    filled = int(round(pct * width))
    return "█" * filled + "░" * (width - filled)

def session_activity(row) -> dict:
    """Horaires de la session affichees, lues chez OpenCode.

    OpenCode stocke time_created / time_updated en millisecondes : les
    passer tels quels a format_age donnait un delta negatif, donc 0s partout.
    Convertis en secondes ici, une fois.

    time_updated - time_created n'est pas un temps de travail : c'est le
    temps pendant lequel la session est restee ouverte. Une session manuelle
    ouverte deux jours affiche donc ~2j, et c'est exact.
    """
    courante = getattr(row, "session_row", None) or {}
    created = int(courante.get("time_created") or 0)
    updated = int(courante.get("time_updated") or 0)
    if not created or not updated or updated < created:
        return {}
    return {"created": created / 1000.0, "updated": updated / 1000.0,
            "duree": (updated - created) / 1000.0}



def esc(text: str) -> str:
    return html.escape(str(text or ""))


def build_dashboard_data(project_root: Path) -> dict:
    state = read_state_minimal(project_root)
    task_id = str(state.get("task_id") or "-")
    status = str(state.get("status") or "-")
    resume = project_root / ".opencode" / "resume"

    pid, age = lock_info(project_root)
    scope, controls = read_plan_scope(project_root)
    archived = read_archived_task_ids(project_root)

    if pid is None:
        watcher_status = "arrêté"
    else:
        watcher_status = f"en cours (PID {pid})"

    agents = []
    db_path = opencode_db_path()
    limits = load_model_limits(project_root)

    for role in ROLE_ORDER:
        agent_file = ROLE_AGENT_FILE.get(role, role)
        report_file = ROLE_REPORT.get(role, f"{role}.txt")

        running = state.get("running_agents")
        running = running if isinstance(running, list) else _flow_items(running or "[]")

        report_path = resume / report_file
        if report_file == "fullstack.txt":
            r_task, r_status = parse_fullstack_section(report_path, role)
        else:
            r_task, r_status, _ = parse_report(report_path)

        report_content = ""
        report_path_obj = resume / report_file
        if report_path_obj.is_file():
            try:
                report_content = report_path_obj.read_text(encoding="utf-8", errors="replace")
            except OSError:
                report_content = "(impossible de lire le rapport)"

        if agent_file in running:
            etat = "EN COURS"
            etat_color = "#17a2b8"
        elif r_task and task_id and r_task == task_id:
            etat = str(r_status or "OK")
            etat_color = "#28a745" if str(r_status or "OK") == "VALIDÉ" else "#ffc107"
        elif r_task:
            etat = "attente"
            etat_color = "#6c757d"
        else:
            etat = "—"
            etat_color = "#888"

        calls = 0
        manual_calls = 0
        origine = "-"
        sid = "-"
        titre = "-"
        usage_pct = "-"
        tokens = "n/d"
        cout_sess = "n/d"
        cout_projet = "n/d"

        if db_path and task_id != "-":
            prefixe = f"Sayrhazi {task_id} {agent_file} "
            sessions = find_sessions(db_path, str(project_root), agent_file, prefixe)
            if sessions:
                auto = latest_auto_session(sessions)
                manuel = latest_manual_session(sessions)
                calls = sum(1 for s in sessions if s.get("auto"))
                manual_calls = sum(1 for s in sessions if not s.get("auto"))

                courante = max((s for s in (auto, manuel) if s),
                               key=lambda s: s.get("time_updated") or s["time_created"])
                origine = "watcher" if courante.get("auto") else "manuel"
                sid = str(courante.get("id", ""))[-10:] if courante.get("id") else "-"
                titre = str(courante.get("title") or "")[:40]

                payload = last_assistant_message(db_path, str(courante["id"]))
                if payload:
                    ctx = message_context_tokens(payload)
                    limit = (limits or {}).get(message_model_key(payload))
                    if isinstance(limit, int) and limit > 0:
                        pct = ctx / limit
                        usage_pct = f"{pct * 100:.0f} %"
                        tokens = f"{ctx:,}".replace(",", " ")
                    cost = courante.get("cost")
                    if cost is not None:
                        cout_sess = f"${float(cost):.2f}"

                cumul = 0.0
                for s in sessions:
                    try:
                        cumul += float(s.get("cost") or 0.0)
                    except (TypeError, ValueError):
                        pass
                    cout_projet = f"${cumul:.2f}"

        agents.append({
            "agent_file": agent_file,
            "role": role,
            "sid": sid,
            "origine": origine,
            "titre": titre,
            "usage_pct": usage_pct,
            "tokens": tokens,
            "cout_sess": cout_sess,
            "calls": calls,
            "manual_calls": manual_calls,
            "cout_projet": cout_projet,
            "report": report_file,
            "rapport_etat": f"{r_status or '-'} · {format_age(0)}" if r_task else "-",
            "etat": etat,
            "etat_color": etat_color,
            "report_content": report_content,
        })

    return {
        "task_id": task_id,
        "status": status,
        "watcher": watcher_status,
        "scope": scope or "non déclaré",
        "controls": controls,
        "archived": sorted(archived),
        "root": str(project_root),
        "agents": agents,
        "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


class Handler(BaseHTTPRequestHandler):
    project_root: Path = Path.cwd()
    interval: int = 2

    def log_message(self, format, *args):
        pass

    def _send_file(self, path: Path, content_type: str):
        try:
            content = path.read_text(encoding="utf-8")
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))
        except OSError:
            self.send_response(404)
            self.end_headers()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path == "/api/data":
            data = build_dashboard_data(self.project_root)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                pass
            return

        if self.path.startswith("/report/"):
            report_file = self.path.split("/report/", 1)[1]
            resume = self.project_root / ".opencode" / "resume"
            report_path = resume / report_file
            if report_path.is_file():
                try:
                    content = report_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    content = "(impossible de lire le rapport)"
            else:
                content = "(rapport introuvable)"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            try:
                self.wfile.write(content.encode("utf-8"))
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                pass
            return

        if self.path == "/api/logs":
            logs_path = self.project_root / ".opencode" / "history" / "workflow-log.md"
            try:
                lines = tail_lines_today(logs_path)
                content = "\n".join(lines) if lines else "Aucun journal aujourd'hui"
            except OSError:
                content = "(journal indisponible)"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            try:
                self.wfile.write(content.encode("utf-8"))
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                pass
            return

        if self.path in ("/", "/index.html"):
            self._send_file(STATIC_DIR / "index.html", "text/html")
            return

        if self.path == "/style.css":
            self._send_file(STATIC_DIR / "style.css", "text/css")
            return

        if self.path == "/app.js":
            self._send_file(STATIC_DIR / "app.js", "application/javascript")
            return

        self.send_response(404)
        self.end_headers()
