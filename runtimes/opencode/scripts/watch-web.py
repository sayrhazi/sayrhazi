"""
watch-web.py — dashboard web Sayrhazi (lecture seule).
Sert une interface HTML sur http://localhost:8000 qui reflète l'état du projet
sayrhazi (rapports, états, agents, journal, watcher).

Usage :
    python .opencode/watch-web.py [--port PORT] [--once]
"""

import argparse
import html
import importlib
import os
import re
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

IS_WINDOWS = os.name == "nt"
DEFAULT_PORT = 8000


def esc(text: str) -> str:
    return html.escape(str(text or ""))


def bar_pct(usage: Optional[float], width: int = 20) -> str:
    if usage is None:
        return f'<span class="bar">{"·" * width}</span>'
    try:
        pct = max(0.0, min(1.0, float(usage)))
    except (TypeError, ValueError):
        return f'<span class="bar">{"·" * width}</span>'
    filled = int(round(pct * width))
    return f'<span class="bar"><span class="filled" style="width:{pct*100:.0f}%"></span></span>'


def status_color(status: Optional[str]) -> str:
    if not status:
        return "#888"
    s = status.upper()
    if "VALIDÉ" in s:
        return "#28a745"
    if "RÉSERVES" in s:
        return "#ffc107"
    if "KO" in s or "ERREUR" in s or "BLOQUÉ" in s:
        return "#dc3545"
    if "EN COURS" in s or "IN_PROGRESS" in s:
        return "#17a2b8"
    if "TERMINÉ" in s:
        return "#6f42c1"
    return "#888"


def _flow_items(text: str) -> list:
    stripped = (text or "").strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        inner = stripped[1:-1].strip()
        if not inner:
            return []
        return [_unquote(p) for p in inner.split(",")]
    return [stripped] if stripped else []


def _unquote(text: str) -> str:
    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in ("'", '"'):
        return stripped[1:-1]
    return stripped


def build_dashboard_data(project_root: Path) -> dict:
    state = read_state_minimal(project_root)
    task_id = str(state.get("task_id") or "-")
    status = str(state.get("status") or "-")
    resume = project_root / ".opencode" / "resume"

    pid, age = lock_info(project_root)
    scope, controls = read_plan_scope(project_root)
    archived = read_archived_task_ids(project_root)

    if pid is None:
        watcher_html = '<span style="color:#dc3545;">●</span> watcher arrêté'
    else:
        watcher_html = f'<span style="color:#28a745;">●</span> watcher PID {pid} · {format_age(age)}'

    scope_html = f'<span style="color:#0d6efd;">scope:</span> {esc(scope or "non déclaré")}'
    controls_html = ""
    if controls:
        parts = []
        for k, v in sorted(controls.items()):
            color = "#28a745" if v == "OK" else "#ffc107"
            parts.append(f'<span style="color:{color};">{esc(k)}={esc(v)}</span>')
        controls_html = " · ".join(parts)
    else:
        controls_html = '<span style="color:#6c757d;">contrôles: —</span>'

    archived_html = f'<span style="color:#6c757d;">archivé:</span> {", ".join(sorted(archived)) or "—"}'

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

        if agent_file in running:
            etat = "EN COURS"
            etat_color = "#17a2b8"
        elif r_task and task_id and r_task == task_id:
            etat = str(r_status or "OK")
            etat_color = status_color(r_status)
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
        })

    return {
        "task_id": task_id,
        "status": status,
        "watcher": watcher_html,
        "scope": scope_html,
        "controls": controls_html,
        "archived": archived_html,
        "root": str(project_root),
        "agents": agents,
        "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


CSS = """
:root { --bg: #0f172a; --panel: #1e293b; --text: #f8fafc; --muted: #94a3b8; --accent: #38bdf8; }
* { box-sizing: border-box; }
body { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; background: var(--bg); color: var(--text); margin: 0; padding: 1rem; }
.header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; padding-bottom: 1rem; border-bottom: 1px solid #334155; }
.header h1 { margin: 0; font-size: 1.25rem; color: var(--accent); }
.meta { display: flex; gap: 1rem; flex-wrap: wrap; font-size: 0.9rem; color: var(--muted); }
.meta span { margin-right: 1rem; }
.table-wrap { overflow-x: auto; margin-bottom: 1rem; }
table { width: 100%; border-collapse: collapse; background: var(--panel); }
th, td { padding: 0.5rem 0.75rem; text-align: left; border-bottom: 1px solid #334155; }
th { background: #0f172a; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: #94a3b8; position: sticky; top: 0; z-index: 1; }
tr:hover { background: #334155; }
td.agent { font-weight: bold; color: #60a5fa; }
td.role { font-size: 0.85rem; color: #cbd5e1; }
td.etat { font-weight: bold; }
.detail { margin-top: 1rem; background: var(--panel); padding: 1rem; border-radius: 0.5rem; }
.detail h3 { margin-top: 0; font-size: 1rem; color: var(--accent); }
.detail pre { background: #020617; padding: 0.75rem; border-radius: 0.4rem; font-size: 0.85rem; overflow: auto; }
.logs { margin-top: 1rem; background: var(--panel); padding: 1rem; border-radius: 0.5rem; }
.logs h3 { margin-top: 0; font-size: 1rem; color: var(--accent); }
.logs pre { background: #020617; padding: 0.75rem; border-radius: 0.4rem; font-size: 0.75rem; color: #94a3b8; overflow: auto; white-space: pre-wrap; }
.refresh { margin-top: 0.5rem; font-size: 0.85rem; color: var(--muted); }
.refresh b { color: var(--accent); cursor: pointer; }
"""

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Sayrhazi — {task_id}</title>
<style>{css}</style>
<meta http-equiv="refresh" content="{interval}">
</head>
<body>
<div class="header">
  <h1>Sayrhazi — <span style="color:{status_color}">{task_id}</span> [{status}]</h1>
  <div class="meta">
    {watcher}<br>
    {scope} · {controls}<br>
    {archived}<br>
    <span>racine: {root}</span><br>
    <span>généré: {now}</span>
  </div>
</div>

<div class="table-wrap">
<table>
<thead>
<tr>
<th>Agent</th><th>Rôle</th><th>Session</th><th>Origine</th><th>Jauge</th><th>Tokens</th><th>%</th><th>Coût sess.</th>
<th>Auto</th><th>Manuel</th><th>Coût proj.</th><th>Rapport</th><th>État</th>
</tr>
</thead>
<tbody>
{rows}
</tbody>
</table>
</div>

<div class="detail">
<h3>Détail de l'agent sélectionné</h3>
<p class="refresh"><b>Rafraîchir</b> la page pour voir l'état actuel.</p>
</div>

<div class="logs">
<h3>Journal (workflow-log.md)</h3>
<pre>{logs}</pre>
</div>
</body>
</html>
"""


def render_html(data: dict, interval: int) -> str:
    rows = []
    for a in data["agents"]:
        rows.append(f"""
<tr>
<td class="agent">{esc(a['agent_file'])}</td>
<td class="role">{esc(a['role'])}</td>
<td>{esc(a['sid'])}</td>
<td>{esc(a['origine'])}</td>
<td>{bar_pct(None)}</td>
<td>{esc(a['tokens'])}</td>
<td>{esc(a['usage_pct'])}</td>
<td>{esc(a['cout_sess'])}</td>
<td>{esc(a['calls'])}</td>
<td>{esc(a['manual_calls'])}</td>
<td>{esc(a['cout_projet'])}</td>
<td>{esc(a['rapport_etat'])}</td>
<td style="color:{a['etat_color']};font-weight:bold;">{esc(a['etat'])}</td>
</tr>
""")

    logs_path = Path(data["root"]) / ".opencode" / "history" / "workflow-log.md"
    try:
        log_lines = tail_lines(logs_path, 10)
        logs_html = "\n".join(esc(l) for l in log_lines) or "(aucun log)"
    except Exception:
        logs_html = "(lecture impossible)"

    return HTML_TEMPLATE.format(
        css=CSS,
        task_id=esc(data["task_id"]),
        status_color=status_color(data["status"]),
        status=esc(data["status"]),
        watcher=data["watcher"],
        scope=data["scope"],
        controls=data["controls"],
        archived=data["archived"],
        root=esc(data["root"]),
        now=data["now"],
        rows="".join(rows),
        interval=interval,
        logs=logs_html,
    )


def tail_lines(path: Path, count: int) -> list:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-max(1, count):] if lines else []


class Handler(BaseHTTPRequestHandler):
    project_root: Path = Path.cwd()
    interval: int = 2

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            data = build_dashboard_data(self.project_root)
            html = render_html(data, self.interval)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
        elif self.path == "/refresh":
            data = build_dashboard_data(self.project_root)
            html = render_html(data, self.interval)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()


def main():
    parser = argparse.ArgumentParser(description="Dashboard web Sayrhazi")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port d'écoute")
    parser.add_argument("--interval", type=int, default=2, help="Intervalle de rafraîchissement (secondes)")
    parser.add_argument("--once", action="store_true", help="Un seul rendu HTML puis sortie")
    args = parser.parse_args()

    root = find_project_root(Path.cwd())
    if root is None:
        print("ERREUR : racine projet introuvable (.opencode/sayrhazi.yaml absent).")
        return 2

    Handler.project_root = root
    Handler.interval = args.interval

    if args.once:
        data = build_dashboard_data(root)
        print(render_html(data, args.interval))
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
    import sys
    sys.exit(main())