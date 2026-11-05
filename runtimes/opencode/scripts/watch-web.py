"""
watch-web.py — dashboard web Sayrhazi (lecture seule).
Sert une interface HTML sur http://localhost:8000 qui reflète l'état du projet
sayrhazi (rapports, états, agents, journal, watcher).

Usage :
    python .opencode/watch-web.py [--port PORT] [--interval SEC] [--root CHEMIN] [--once]
"""

import argparse
import html
import importlib
import json
import os
import sys
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional

# Réutilisation des fonctions de lecture depuis watch-tui.py (fichier avec tiret)
_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))
_wt = importlib.import_module("watch-tui")

find_project_root = _wt.find_project_root
read_state_minimal = _wt.read_state_minimal
read_plan_scope = _wt.read_plan_scope
read_archived_task_ids = _wt.read_archived_task_ids
lock_info = _wt.lock_info
opencode_db_path = _wt.opencode_db_path
find_sessions = _wt.find_sessions
last_auto_session = _wt.latest_auto_session
last_manual_session = _wt.latest_manual_session
last_assistant_message = _wt.last_assistant_message
message_context_tokens = _wt.message_context_tokens
message_model_key = _wt.message_model_key
format_age = _wt.format_age
format_span = _wt.format_span
short_session = _wt.short_session
load_model_limits = _wt.load_model_limits
ROLE_ORDER = _wt.ROLE_ORDER
ROLE_AGENT_FILE = _wt.ROLE_AGENT_FILE
ROLE_REPORT = _wt.ROLE_REPORT
parse_report = _wt.parse_report
parse_fullstack_section = _wt.parse_fullstack_section
_flow_items = _wt._flow_items
_unquote = _wt._unquote

IS_WINDOWS = os.name == "nt"
DEFAULT_PORT = 8000

STATIC_DIR = _here / "static"


def _find_project_root(start: Path) -> Optional[Path]:
    """Remonte depuis start pour trouver .opencode/sayrhazi.yaml (sans fallback script)."""
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".opencode" / "sayrhazi.yaml").is_file():
            return candidate
    return None


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
                auto = last_auto_session(sessions)
                manuel = last_manual_session(sessions)
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


def tail_lines(path: Path, count: int) -> list:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-max(1, count):] if lines else []


def tail_lines_today(path: Path) -> list:
    """Retourne les lignes du dernier jour travaillé dans le journal."""
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


def main():
    parser = argparse.ArgumentParser(description="Dashboard web Sayrhazi")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port d'écoute")
    parser.add_argument("--interval", type=int, default=2, help="Intervalle de rafraîchissement (secondes)")
    parser.add_argument("--root", type=str, default=None, help="Racine du projet Sayrhazi (défaut : détectée depuis le répertoire courant)")
    parser.add_argument("--once", action="store_true", help="Un seul rendu JSON puis sortie")
    args = parser.parse_args()

    if args.root:
        root = Path(args.root).resolve()
    else:
        root = _find_project_root(Path.cwd())
    if root is None:
        print("ERREUR : racine projet introuvable (.opencode/sayrhazi.yaml absent).")
        print("       Utilisez --root <chemin> pour spécifier la racine manuellement.")
        return 2

    Handler.project_root = root
    Handler.interval = args.interval

    if args.once:
        data = build_dashboard_data(root)
        print(json.dumps(data, ensure_ascii=False))
        return 0

    print(f"Sayrhazi web — http://localhost:{args.port}/")
    print(f"Racine : {root}")
    print("Ctrl+C pour arrêter.")
    try:
        HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())