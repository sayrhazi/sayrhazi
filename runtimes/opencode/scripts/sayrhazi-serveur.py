"""
sayrhazi-serveur.py - serveur unifié Sayrhazi : dashboard web + moteur watcher.

Lance un serveur HTTP sur http://localhost:8363 servant le dashboard de
sayrhazi-modules.py, et permet de démarrer/arrêter le moteur watch-work.py depuis
l'interface web (endpoints POST /api/worker/start et /api/worker/stop).

Un seul processus Python, un seul terminal. Le verrou
.opencode/state/watcher.lock gère l'exclusivité entre instances.

Usage :
    python .opencode/sayrhazi-serveur.py [--port PORT] [--root CHEMIN]
"""

import argparse
import ast
import base64
import importlib
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import difflib
import urllib.error
import urllib.request
from http.server import HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

# Modules existants : pas de copie, pas de duplication de code.
# watch-work.py : moteur watcher des boutons Marche/Arrêt - INTOUCHÉ.
# sayrhazi-modules.py : briques communes (lecture OpenCode + base Handler du
# dashboard). sayrhazi-modules.py regroupe les briques communes lecture OpenCode + Handler web.
_mod = importlib.import_module("sayrhazi-modules")
_ww = importlib.import_module("watch-work")

DEFAULT_PORT = 8363
STATIC_DIR = _here / "static"
NODE_MODULES_DIR = _here / "node_modules"
IS_WINDOWS = os.name == "nt"

# Forcer les couleurs ANSI dans le watcher même si stdout est redirigé
# (le dashboard les convertira en HTML via app.js).
os.environ["FORCE_COLOR"] = "1"

# Fonctions du moteur watch-work (espaces de noms séparés pour éviter les
# collisions avec sayrhazi-modules / watch-work qui définissent les mêmes noms).
acquire_lock = _ww.acquire_lock
release_lock = _ww.release_lock
touch_lock = _ww.touch_lock
lock_holder_pid = _ww.lock_holder_pid
read_conditions = _ww.read_conditions
parse_workflow_minimal = _ww.parse_workflow_minimal
select_auto_stages = _ww.select_auto_stages
eligible_transitions = _ww.eligible_transitions
run_branches = _ww.run_branches
max_parallel_agents = _ww.max_parallel_agents
watched_reports = _ww.watched_reports
parse_report_file = _ww.parse_report_file
file_hash = _ww.file_hash
write_state_minimal = _ww.write_state_minimal
journal = _ww.journal
agent_model = _ww.agent_model
ROLE_REPORT_WW = _ww.ROLE_REPORT
ROLE_AGENT_FILE_WW = _ww.ROLE_AGENT_FILE
POLL_INTERVAL_SECONDS = _ww.POLL_INTERVAL_SECONDS

# Fonctions de lecture de la base OpenCode depuis sayrhazi-modules (déjà utilisées pour le web).
opencode_db_path = _mod.opencode_db_path
find_sessions = _mod.find_sessions
last_assistant_message = _mod.last_assistant_message
message_context_tokens = _mod.message_context_tokens
message_model_key = _mod.message_model_key
load_model_limits = _mod.load_model_limits

try:
    _agent_sync = importlib.import_module("agent_sync")
    sync_home_opencode = _agent_sync.sync_home_opencode
except Exception:
    sync_home_opencode = None


class _LogCapture(io.TextIOBase):
    """Capture les écritures sur stdout/stderr et garde les N dernières lignes."""

    def __init__(self, max_lines: int = 500):
        self.max_lines = max_lines
        self._lines: list[str] = []
        self._lock = threading.Lock()

    def write(self, text: str) -> int:
        if not text:
            return 0
        for line in text.splitlines():
            if line.strip():
                with self._lock:
                    self._lines.append(line)
                    if len(self._lines) > self.max_lines:
                        self._lines = self._lines[-self.max_lines:]
        return len(text)

    def flush(self) -> None:
        pass

    def get_lines(self) -> list[str]:
        with self._lock:
            return list(self._lines)


watcher_log_capture = _LogCapture()


class WatcherEngine:
    """Contrôle du moteur watcher dans un thread dédié, pilotable via l'API."""

    def __init__(self, root: Path):
        self.root = root
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.running = False
        self.config: Optional[dict] = None
        self.usable: list = []

    def start(self, config: Optional[dict], usable: list) -> bool:
        with self._lock:
            if self.running:
                return False
            self.config = config
            self.usable = usable
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, args=(config, usable), daemon=False,
                name="sayrhazi-watcher",
            )
            self._thread.start()
            self.running = True
            return True

    def stop(self, timeout: float = 15.0) -> bool:
        with self._lock:
            if not self.running:
                return False
            self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        with self._lock:
            self.running = False
        return True

    def status(self) -> dict:
        with self._lock:
            return {"running": self.running, "pid": os.getpid()}

    def get_logs(self) -> list[str]:
        return watcher_log_capture.get_lines()

    def _loop(self, config: Optional[dict], usable: list) -> None:
        # Rediriger stdout/stderr vers la capture pendant toute la vie du watcher
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout = watcher_log_capture
        sys.stderr = watcher_log_capture
        try:
            holder = lock_holder_pid(self.root)
            if holder is not None and holder != os.getpid():
                print(f"[watcher] verrou détenu par PID {holder}, démarrage impossible.")
                with self._lock:
                    self.running = False
                return
            if not acquire_lock(self.root):
                with self._lock:
                    self.running = False
                return
            try:
                watched = watched_reports(usable)
                resume = self.root / ".opencode" / "resume"
                last_keys = set()
                last_hashes = {}
                print(
                    f"[watcher] surveillance de : "
                    f"{', '.join(watched) if watched else 'aucun rapport'}"
                )
                while not self._stop.is_set():
                    self._cycle(watched, resume, last_keys, last_hashes)
                    self._stop.wait(POLL_INTERVAL_SECONDS)
                    touch_lock(self.root)
            finally:
                release_lock(self.root)
                with self._lock:
                    self.running = False
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

    def _cycle(self, watched: list, resume: Path, last_keys: set, last_hashes: dict) -> None:
        triggers: dict = {}
        outputs: dict = {}
        for name in watched:
            path = resume / name
            task_id, status, mtime = parse_report_file(path)
            if task_id:
                triggers[name] = {
                    "task_id": task_id,
                    "status": status,
                    "mtime": mtime or 0.0,
                    "hash": file_hash(path),
                }
        for name in ROLE_REPORT_WW.values():
            path = resume / name
            if path.is_file():
                task_id, _status, mtime = parse_report_file(path)
                if task_id:
                    outputs[name] = {"task_id": task_id, "mtime": mtime or 0.0}
        conditions = read_conditions(self.root)
        found = eligible_transitions(
            {"stages": self.usable} if self.usable else {}, triggers, conditions, outputs
        )
        todo = []
        for stage, task_id, why in found:
            trigger_report = (stage.get("trigger") or {}).get("report")
            key = (
                f"{stage.get('id')}:{task_id}:"
                f"{triggers.get(trigger_report, {}).get('hash')}"
            )
            if key in last_keys:
                continue
            todo.append((key, stage, task_id, why))
        if todo:
            workers = min(max_parallel_agents(self.config), len(todo))
            run_branches(self.root, todo, workers)
            for key, _, _, _ in todo:
                last_keys.add(key)


class OpenCodeServer:
    """Lance et pilote `opencode serve` (mode serveur sans TUI).

    Expose les endpoints HTTP/SSE d'OpenCode pour créer des sessions
    persistantes, envoyer des messages et lire les réponses en temps réel.
    """

    def __init__(self, root: Path, port: int = 4096):
        self.root = root
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        self.process: Optional[subprocess.Popen] = None

    def start(self) -> bool:
        """Lance `opencode serve` en arrière-plan si ce n'est pas déjà fait."""
        if self.process is not None and self.process.poll() is None:
            return True
        try:
            self.process = subprocess.Popen(
                ["opencode", "serve", "--hostname", "127.0.0.1", "--port", str(self.port)],
                cwd=str(self.root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=IS_WINDOWS,
            )
            return True
        except FileNotFoundError:
            return False

    def stop(self) -> None:
        if self.process is not None:
            try:
                self.process.terminate()
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            except Exception:
                pass
            self.process = None

    def _request(self, method: str, path: str, data: Optional[dict] = None,
                 timeout: int = 120) -> tuple[int, Optional[dict]]:
        """Appelle l'API OpenCode Server. Retourne (status_code, data_or_None)."""
        url = self.base + path
        body = None
        headers = {"Content-Type": "application/json"}
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                try:
                    return resp.status, json.loads(text) if text else {}
                except json.JSONDecodeError:
                    return resp.status, {"raw": text}
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", errors="replace")
            try:
                return e.code, json.loads(text) if text else {}
            except json.JSONDecodeError:
                return e.code, {"raw": text}
        except Exception as e:
            return 0, {"error": str(e)}

    def create_session(self, agent_file: str, directory: str = "") -> tuple[int, Optional[dict]]:
        """Crée une session OpenCode native, sans titre.

        Le titre est délibérément absent : OpenCode le génère lui-même après le
        premier message utilisateur (agent natif `title`, `ensureTitle`), comme
        dans la TUI. Un titre fourni ici empêcherait ce renommage.
        """
        return self._request("POST", "/session" + self._directory_query(directory), {
            "agent": agent_file,
        })

    def get_session(self, session_id: str, directory: str = "") -> tuple[int, Optional[dict]]:
        """Récupère une session OpenCode (titre, agent, parent...)."""
        return self._request("GET", f"/session/{session_id}" + self._directory_query(directory))

    def list_sessions(self, directory: str = "") -> tuple[int, Optional[dict]]:
        """Liste les sessions OpenCode d'un répertoire."""
        return self._request("GET", "/session" + self._directory_query(directory))

    def delete_session(self, session_id: str, directory: str = "") -> tuple[int, Optional[dict]]:
        """Supprime une session via l'endpoint natif DELETE /session/{id}.

        OpenCode supprime aussi les sessions enfants (récursion native) et
        publie l'événement `session.deleted`. Les données ne sont jamais
        effacées directement en SQLite par Sayrhazi.
        """
        return self._request("DELETE", f"/session/{session_id}" + self._directory_query(directory))

    def send_message(self, session_id: str, message: str, agent: str = "",
                     directory: str = "") -> tuple[int, Optional[dict]]:
        """Envoie un message dans une session OpenCode (appel synchrone)."""
        payload = {"parts": [{"type": "text", "text": message}]}
        if agent:
            payload["agent"] = agent
        return self._request("POST", f"/session/{session_id}/message"
                             + self._directory_query(directory), payload)

    def get_messages(self, session_id: str) -> tuple[int, Optional[dict]]:
        """Récupère les messages d'une session."""
        return self._request("GET", f"/session/{session_id}/message")

    def prompt_async(self, session_id: str, message: str, agent: str = "",
                     directory: str = "", model: Optional[dict] = None,
                     attachments: Optional[list] = None,
                     root: Optional[Path] = None) -> tuple[int, Optional[dict]]:
        """Envoie un message de façon asynchrone (streaming) dans une session OpenCode.

        L'agent est transmis pour que la session reste rattachée à l'agent
        choisi dans Sayrhazi (sans lui, OpenCode bascule sur l'agent par
        défaut et la session quitte la barre latérale de l'agent d'origine).
        Le modèle (choix explicite dans l'UI) est transmis tel quel au prompt
        OpenCode ({providerID, modelID}), comme dans la TUI ; sinon OpenCode
        applique le modèle configuré de la session/agent.

        Les pièces jointes `attachments: [{path, startLine, endLine} | {url, filename}]`
        sont relues côté serveur pour les pièces filesystem (`path`, sources de
        mention), ou passées telles quelles à OpenCode pour les pièces uploadées
        du browsers (`url: data:...`, sources clients).
        """
        parts = [{"type": "text", "text": message}]
        if attachments:
            for att in attachments:
                if not isinstance(att, dict):
                    continue
                if att.get("url"):
                    url = str(att.get("url") or "")
                    name = str(att.get("filename") or att.get("name") or "")
                    if not name:
                        name = os.path.basename(url) if url else ""
                    part = {
                        "type": "file",
                        "mime": str(att.get("mime") or "text/plain"),
                        "url": url,
                        "filename": name,
                    }
                    mention_value = str(att.get("mention") or ("@" + (name or "fichier")))
                    mention_start = message.find(mention_value)
                    if mention_start < 0:
                        mention_start = len(message)
                    part["source"] = {
                        "type": "file",
                        "text": {"value": mention_value, "start": mention_start,
                                 "end": mention_start + len(mention_value)},
                        "path": url,
                    }
                    parts.append(part)
                    continue
                if not att.get("path"):
                    continue
                raw_path = str(att.get("path"))
                base = Path(root) if root else Path.cwd()
                full = Path(raw_path)
                if not full.is_absolute():
                    full = base / full
                if not full.exists():
                    continue
                try:
                    if full.is_dir():
                        entries = sorted(full.iterdir(), key=lambda p: p.name.lower())
                        lines = []
                        for i, entry in enumerate(entries):
                            if i >= 200:
                                lines.append("...")
                                break
                            lines.append(("📁 " if entry.is_dir() else "📄 ") + entry.name)
                        content = "\n".join(lines) or "(répertoire vide)"
                        is_dir = True
                    else:
                        content = full.read_text(encoding="utf-8", errors="replace")
                        is_dir = False
                except OSError:
                    continue
                mime = "text/plain"
                data_url = "data:text/plain;base64," + base64.b64encode(content.encode("utf-8")).decode("ascii")
                mention_value = "@" + raw_path
                mention_start = message.find(mention_value)
                if mention_start < 0:
                    mention_start = len(message)
                parts.append({
                    "type": "file",
                    "mime": mime,
                    "url": data_url,
                    "filename": full.name or raw_path,
                    "directory": is_dir,
                    "source": {
                        "type": "file",
                        "text": {"value": mention_value, "start": mention_start,
                                 "end": mention_start + len(mention_value)},
                        "path": raw_path,
                    },
                })
                continue

        payload = {"parts": parts}
        if agent:
            payload["agent"] = agent
        if isinstance(model, dict):
            provider_id = str(model.get("providerID") or "").strip()
            model_id = str(model.get("modelID") or "").strip()
            if provider_id and model_id:
                payload["model"] = {"providerID": provider_id, "modelID": model_id}
        return self._request("POST", f"/session/{session_id}/prompt_async"
                             + self._directory_query(directory), payload)

    def revert_session(self, session_id: str, message_id: str,
                       directory: str = "", part_id: str = "") -> tuple[int, Optional[dict]]:
        """Revert natif d'une conversation (« Revert - undo messages and file changes »).

        OpenCode stocke `session.revert = {messageID, snapshot, diff}` et
        restaure les fichiers vers le snapshot ; les messages ne sont pas
        supprimés (ils disparaissent à l'envoi suivant - cleanup natif).
        """
        payload = {"messageID": message_id}
        if part_id:
            payload["partID"] = part_id
        return self._request("POST", f"/session/{session_id}/revert"
                             + self._directory_query(directory), payload)

    def unrevert_session(self, session_id: str,
                         directory: str = "") -> tuple[int, Optional[dict]]:
        """Annule un revert en attente : restaure fichiers + session.revert → null."""
        return self._request("POST", f"/session/{session_id}/unrevert"
                             + self._directory_query(directory))

    def abort_session(self, session_id: str,
                      directory: str = "") -> tuple[int, Optional[dict]]:
        """Interrompt le traitement en cours sur la session (POST /session/:id/abort).

        L'agent s'arrête (outil ou réponse coupés) ; la session repasse en idle.
        """
        return self._request("POST", f"/session/{session_id}/abort"
                             + self._directory_query(directory))

    def respond_permission(self, request_id: str, reply: str,
                           message: str = "") -> tuple[int, Optional[dict]]:
        payload = {"reply": reply}
        if message:
            payload["message"] = message
        return self._request("POST", f"/permission/{request_id}/reply", payload)

    def respond_question(self, request_id: str, answers) -> tuple[int, Optional[dict]]:
        return self._request("POST", f"/question/{request_id}/reply",
                             {"answers": answers})

    def reject_question(self, request_id: str) -> tuple[int, Optional[dict]]:
        return self._request("POST", f"/question/{request_id}/reject", {})

    def list_permissions(self, directory: str = "") -> tuple[int, Optional[dict]]:
        return self._request("GET", f"/permission" + self._directory_query(directory))

    def list_questions(self, directory: str = "") -> tuple[int, Optional[dict]]:
        return self._request("GET", f"/question" + self._directory_query(directory))

    def rename_session(self, session_id: str, title: str,
                       directory: str = "") -> tuple[int, Optional[dict]]:
        """Renomme une session via PATCH /session/{id} (titre natif)."""
        return self._request("PATCH", f"/session/{session_id}"
                             + self._directory_query(directory), {"title": title})

    def global_event(self) -> tuple[int, Optional[dict]]:
        """Récupère les événements SSE globaux d'OpenCode Server."""
        return self._request("GET", "/global/event")

    def _directory_query(self, directory: str) -> str:
        """Paramètre `directory` de l'API OpenCode (chemin normalisé)."""
        if not directory:
            return ""
        import urllib.parse
        return "?directory=" + urllib.parse.quote(str(directory).replace("\\", "/"))


# Instance unique du moteur, partagée avec le handler HTTP et le signal handler.
watcher_engine: Optional[WatcherEngine] = None
opencode_server: Optional[OpenCodeServer] = None
_http_server: Optional[HTTPServer] = None

CHAT_SESSION_PREFIX = "sayrhazi-chat-"


def _is_default_session_title(title: str) -> bool:
    """Vrai si le titre est le titre par défaut d'OpenCode.

    OpenCode titre une session neuve « New session - <horodatage> » (ou
    « Child session - <horodatage> » pour une session enfant) et ne la
    renomme qu'après le premier message utilisateur (agent natif `title`).
    Tant que le titre est celui-là, la discussion n'a pas encore de nom.
    """
    if not title:
        return True
    return bool(re.match(r"^(New session|Child session) - \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$",
                         title.strip()))


def _chat_agent_display_name(agent_file: str) -> str:
    """Nom d'affichage d'un agent pour le chat."""
    mapping = {
        "lawibrahim": "Lawibrahim",
        "ali": "Ali",
        "bamse": "Bamse",
        "hadji": "Hadji",
        "hifadhui": "Hifadhui",
        "zawadi": "Zawadi",
    }
    return mapping.get(agent_file, agent_file.capitalize())


def build_chat_command(agent_file: str, message: str, model: str, root: Path) -> list:
    """Construire la commande `opencode run` pour le chat avec session persistante."""
    full_model, model_short = agent_model(root, agent_file)
    title = f"Sayrhazi Chat {agent_file}"
    session_id = CHAT_SESSION_PREFIX + agent_file
    cmd = ["opencode", "run", "--agent", agent_file, "--session", session_id,
           "--title", title]
    if model or full_model:
        cmd += ["--model", model or full_model]
    return cmd + [message]


def run_chat(root: Path, agent_file: str, message: str) -> dict:
    """Exécute un appel chat vers un agent via `opencode run` avec session persistante.

    Retourne {"ok": bool, "response": str, "timestamp": str, "error": str|None}.
    """
    from datetime import datetime
    full_model, model_short = agent_model(root, agent_file)
    cmd = build_chat_command(agent_file, message, full_model or "", root)
    if IS_WINDOWS:
        command_str = " ".join(f'"{p}"' if " " in p else p for p in cmd)
        kwargs = dict(cwd=str(root), capture_output=True, text=True, shell=True,
                      timeout=120, encoding="utf-8", errors="replace")
        result = subprocess.run(command_str, **kwargs)
    else:
        result = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True,
                                timeout=120, encoding="utf-8", errors="replace")

    timestamp = datetime.now().strftime("%H:%M")
    if result.returncode != 0:
        err = result.stderr or "opencode erreur " + str(result.returncode)
        return {"ok": False, "response": "", "timestamp": timestamp, "error": err}

    # Récupérer le dernier message assistant de la session depuis la base OpenCode
    response_text = result.stdout.strip() if result.stdout else ""
    if not response_text:
        # Essayer de lire depuis la base OpenCode
        try:
            db_path = opencode_db_path()
            if db_path:
                prefixe = f"Sayrhazi Chat {agent_file} "
                sessions = find_sessions(db_path, str(root), agent_file, prefixe)
                if sessions:
                    courante = sessions[0]
                    payload = last_assistant_message(db_path, str(courante.get("id", "")))
                    if payload:
                        ctx = message_context_tokens(payload)
                        # Extraire le texte du message assistant
                        if isinstance(payload, dict):
                            content = payload.get("content") or payload.get("message") or ""
                            if content:
                                response_text = content
        except Exception:
            pass

    if not response_text:
        response_text = "(pas de réponse)"

    return {"ok": True, "response": response_text, "timestamp": timestamp, "error": None}


def _extract_text_from_data(data_json: str) -> str:
    """Extrait le texte d'un message depuis le champ JSON `data` d'OpenCode."""
    if not data_json:
        return ""
    payload = _parse_tool_data(data_json)
    if not isinstance(payload, dict):
        return ""
    # Essayer plusieurs clés possibles pour le texte
    text = (
        payload.get("content")
        or payload.get("message")
        or payload.get("text")
        or ""
    )
    if text:
        return text
    # Certains messages OpenCode utilisent une structure "parts"
    parts = payload.get("parts")
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, dict) and part.get("text"):
                return part["text"]
    return ""


def _find_text_recursive(obj: object, seen: set | None = None) -> str:
    """Cherche recursivement une valeur string contenant du texte dans un JSON.

    Retourne la premiere chaine trouvee qui ressemble a du contenu de message
    (pas un UUID, chemin, ID, timestamp, etc.).
    """
    if seen is None:
        seen = set()
    if id(obj) in seen:
        return ""
    seen.add(id(obj))

    if isinstance(obj, str):
        s = obj.strip()
        if not s:
            return ""
        # Ignorer les identificateurs, chemins, timestamps, model IDs
        if len(s) < 3:
            return ""
        if s.startswith(("msg_", "ses_", "http://", "https://")):
            return ""
        if all(c in "0123456789abcdef-" for c in s) and len(s) >= 8:
            return ""
        if s.startswith("C:\\") or s.startswith("/"):
            return ""
        return s
    if isinstance(obj, dict):
        # Prioriser les cles connues qui contiennent du texte
        for key in ("content", "text", "message", "result", "output", "body",
                    "data", "answer", "response"):
            if key in obj:
                val = _find_text_recursive(obj[key], seen)
                if val:
                    return val
        for val in obj.values():
            found = _find_text_recursive(val, seen)
            if found:
                return found
    if isinstance(obj, list):
        for item in obj:
            found = _find_text_recursive(item, seen)
            if found:
                return found
    return ""


def _extract_metrics(payload: dict) -> dict:
    """Extrait les metriques utiles d'un message OpenCode pour affichage."""
    out = {}
    tokens = payload.get("tokens")
    if isinstance(tokens, dict):
        out["tokens_total"] = tokens.get("total")
        out["tokens_input"] = tokens.get("input")
        out["tokens_output"] = tokens.get("output")
        out["tokens_reasoning"] = tokens.get("reasoning")
    cost = payload.get("cost")
    if cost is not None:
        try:
            out["cost"] = float(cost)
        except (TypeError, ValueError):
            pass
    model = payload.get("modelID")
    if not model or not isinstance(model, str):
        m = payload.get("model")
        if isinstance(m, dict):
            model = str(m.get("modelID") or "")
        elif isinstance(m, str):
            model = m
    if model:
        out["model"] = str(model)
    finish = payload.get("finish")
    if finish:
        out["finish"] = str(finish)
    mode = payload.get("mode")
    if mode:
        out["mode"] = str(mode)
    return out


def _parse_message_payload(payload: dict) -> tuple[str, str, bool]:
    """Extrait (role, text, metrics_only) depuis un payload JSON OpenCode.

    metrics_only est True quand aucun texte reel n'est stocke et que seul un
    substitut de metriques est affiche.
    """
    if not isinstance(payload, dict):
        return "system", "", False

    role = str(payload.get("role") or "")
    if not role:
        msg = payload.get("message")
        if isinstance(msg, dict):
            role = str(msg.get("role") or "")
    if not role:
        role = "system"
    # Mapper les roles OpenCode vers les classes CSS du chat
    role = {"user": "user", "assistant": "agent"}.get(role, role)

    text = ""
    # 1. champs directs a la racine
    for key in ("content", "text", "message"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            text = val
            break
    # 2. contenu dans message.content / message.text
    if not text:
        msg = payload.get("message")
        if isinstance(msg, dict):
            for key in ("content", "text"):
                val = msg.get(key)
                if isinstance(val, str) and val.strip():
                    text = val
                    break
    # 3. contenu dans data.content / data.text
    if not text:
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("content", "text"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    text = val
                    break
    # 4. contenu dans parts[].text / parts[].content
    if not text:
        parts = payload.get("parts")
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict):
                    for key in ("text", "content"):
                        val = part.get(key)
                        if isinstance(val, str) and val.strip():
                            text = val
                            break
                    if text:
                        break
    # 5. content peut etre un objet {text: ...} ou {parts: [...]}
    if not text and isinstance(payload.get("content"), dict):
        c = payload["content"]
        if isinstance(c.get("text"), str) and c["text"].strip():
            text = c["text"]
        elif isinstance(c.get("parts"), list):
            for part in c["parts"]:
                if isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"].strip():
                    text = part["text"]
                    break

    # 7. Aucun texte stocke : retourner les metriques pour affichage substitutif
    metrics_only = False
    if not text:
        metrics_only = True
        m = _extract_metrics(payload)
        parts_metrics = []
        if m.get("tokens_total") is not None:
            parts_metrics.append(f"{m['tokens_total']:,} tok".replace(",", " "))
        if m.get("cost") is not None:
            parts_metrics.append(f"${m['cost']:.2f}")
        if m.get("model"):
            parts_metrics.append(m["model"])
        if m.get("finish"):
            parts_metrics.append(f"[{m['finish']}]")
        if parts_metrics:
            text = " • ".join(parts_metrics)
        else:
            text = "(message sans contenu)"
    return role, text, metrics_only


def get_chat_history(root: Path, agent_file: str) -> list:
    """Récupère l'historique des conversations avec un agent depuis la base OpenCode.

    Pour chaque session de l'agent, affiche le dernier message (utilisateur ou
    assistant) avec ses métriques (tokens, coût, modèle). Aucune filtre sur le
    répertoire : les sessions sont partagées entre tous les projets OpenCode.
    """
    messages: list = []
    try:
        import sqlite3
        import json
        db_path = opencode_db_path()
        if not db_path:
            return messages
        db = sqlite3.connect("file:" + str(db_path) + "?mode=ro", uri=True, timeout=5)
        try:
            cur = db.cursor()
            # Scoper les sessions au projet courant (dossier racine) : un
            # agent ouvert dans un projet ne doit pas voir les sessions des
            # autres projets. Le format de directory est celui stocke par
            # OpenCode (barres obliques).
            cur.execute(
                "SELECT id, title, cost, time_created, time_updated FROM session"
                " WHERE directory = ? AND agent = ?"
                " ORDER BY time_updated DESC",
                ((str(root) or "").replace("\\", "/"), agent_file))
            rows = cur.fetchall()
        finally:
            db.close()
    except Exception:
        return messages

    # La plus récente en haut (ordre chronologique inverse, comme la sidebar des discussions).
    sessions = list(rows)[:10]

    for sid, title, cost, time_created, time_updated in sessions:
        try:
            session_id = str(sid or "")
            t = _format_time_ms(time_updated or time_created or "")
            title_s = str(title or "")

            row = None
            try:
                db = sqlite3.connect("file:" + str(db_path) + "?mode=ro", uri=True, timeout=5)
                try:
                    cur = db.cursor()
                    # La colonne role/content n'existe pas dans cette version
                    # d'OpenCode : tout est dans le JSON `data`.
                    cur.execute(
                        "SELECT data FROM message WHERE session_id = ?"
                        " ORDER BY time_created DESC LIMIT 1",
                        (session_id,))
                    row = cur.fetchone()
                finally:
                    db.close()
            except Exception:
                row = None

            if not row:
                # Pas de message dans cette session : afficher un placeholder
                messages.append({
                    "role": "system",
                    "text": f"Session {title_s} ouverte (aucun message stocké).",
            "time": t,
            "created": time_created or time_updated,
            "session": session_id,
                    "title": title_s,
                    "default_title": _is_default_session_title(title_s),
                    "tokens": "n/d",
                    "tokens_pct": "n/d",
                    "tokens_limit": None,
                    "model": "n/d",
                    "cost": f"${float(cost):.2f}" if cost is not None else "n/d",
                    "auto": str(title_s or "").startswith("Sayrhazi Chat"),
                })
                continue

            data_json = row[0]
            payload = _parse_tool_data(data_json) if data_json else {}
            role, text, metrics_only = _parse_message_payload(payload)

            tokens = 0
            model_key = ""
            try:
                if isinstance(payload, dict):
                    tokens_dict = payload.get("tokens") or {}
                    cache = tokens_dict.get("cache") or {}
                    tokens = (
                        _num(tokens_dict.get("input")) + _num(tokens_dict.get("output"))
                        + _num(tokens_dict.get("reasoning")) + _num(cache.get("read"))
                        + _num(cache.get("write"))
                    )
                    provider = str(payload.get("providerID") or "").strip()
                    model = str(payload.get("modelID") or "").strip()
                    if not model:
                        nested = payload.get("model") or {}
                        provider = provider or str(nested.get("providerID") or "").strip()
                        model = str(nested.get("modelID") or "").strip()
                    model_key = f"{provider}/{model}" if provider else model
            except (ValueError, TypeError, AttributeError):
                pass

            pct = None
            try:
                limit = (load_model_limits(root) or {}).get(model_key) if model_key else None
                if isinstance(limit, int) and limit > 0 and tokens > 0:
                    pct = tokens / limit
            except Exception:
                pass

            cost_str = "n/d"
            try:
                if cost is not None:
                    cost_str = f"${float(cost):.2f}"
            except (TypeError, ValueError):
                pass

            messages.append({
                "role": role,
                "text": text if text else "(message vide)",
            "time": t,
            "created": time_created or time_updated,
            "session": session_id,
                "title": title_s,
                "default_title": _is_default_session_title(title_s),
                "tokens": tokens,
                "tokens_pct": f"{pct * 100:.0f}" if isinstance(pct, float) else "n/d",
                "tokens_limit": limit,
                "model": model_key or "n/d",
                "cost": cost_str,
                "auto": str(title_s or "").startswith("Sayrhazi Chat"),
                "metrics_only": metrics_only,
            })
        except Exception:
            pass
    return messages


def _num(value) -> int:
    """Convertit une valeur en entier, 0 par défaut."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _parse_tool_data(data_json):
    """Parse une donnée de part OpenCode : JSON pur ou repr Python (tool [...]).

    OpenCode stocke parfois les tool sous forme de repr Python
    (ex: write [{'status': ..., 'input': {...}}]) plutôt qu'en JSON pur.
    """
    if not data_json:
        return {}
    try:
        result = json.loads(data_json)
        if isinstance(result, list) and result:
            return result[0]
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        pass
    try:
        m = re.search(r"\[.*\]", data_json.strip(), re.DOTALL)
        if m:
            result = ast.literal_eval(m.group(0))
            if isinstance(result, list) and result:
                return result[0]
            return result if isinstance(result, dict) else {}
    except (ValueError, TypeError, SyntaxError):
        pass
    return {}


# Cache du catalogue providers OpenCode (nom lisible des modeles).
_PROVIDER_MODELS_CACHE: Optional[dict] = None


def _provider_model_names() -> dict:
    """Retourne {providerID: {modelID: name}} depuis l'API OpenCode (cache en memoire).

    L'API /provider expose la liste des providers sous la cle "all"
    (et "connected" pour ceux qui sont actives).
    """
    global _PROVIDER_MODELS_CACHE
    if _PROVIDER_MODELS_CACHE is not None:
        return _PROVIDER_MODELS_CACHE
    names: dict = {}
    try:
        req = urllib.request.Request("http://127.0.0.1:4096/provider", method="GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        providers = list(data.get("all") or []) + list(data.get("connected") or [])
        for provider in providers:
            if not isinstance(provider, dict):
                continue
            pid = str(provider.get("id") or "")
            if not pid:
                continue
            bucket = names.setdefault(pid, {})
            for model_id, model in (provider.get("models") or {}).items():
                if isinstance(model, dict) and model.get("name"):
                    bucket[str(model_id)] = str(model["name"])
    except Exception:
        pass
    _PROVIDER_MODELS_CACHE = names
    return names


def _resolve_model_name(provider_id: str, model_id: str) -> str:
    """Nom lisible du modele (comme Ed() d'OpenCode), fallback sur modelID."""
    if not model_id:
        return ""
    if provider_id:
        try:
            name = _provider_model_names().get(provider_id, {}).get(model_id)
            if name:
                return name
        except Exception:
            pass
    return model_id


# Catalogue providers OpenCode (appelé par la GUI et pour résoudre le nom
# affichable du fournisseur d'un modèle). Cache court : le catalogue change
# rarement mais Sayrhazi ne veut pas servir un catalogue figé.
_PROVIDER_CATALOG_CACHE: Optional[list] = None
_PROVIDER_CATALOG_AT = 0.0
_PROVIDER_CATALOG_TTL = 60.0


# Tri canonique des variantes (minimum -> maximum) ; variantes hors liste à la fin, alphabetiquement.
_REASONING_SCALE = ["minimal", "low", "medium", "high", "max", "xhigh", "none"]

MODELS_REGISTRY_HEADER = """# Registre des fenêtres de contexte (lecture seule, observé par le dashboard).
#
# Pourquoi ce fichier : OpenCode n'enregistre nulle part la taille de la
# fenêtre de contexte d'un modèle (ni en base, ni dans sa configuration), mais
# son panneau Context en a besoin pour afficher son pourcentage. Sayrhazi
# reproduit ce calcul : sans limite connue, il n'affiche aucun pourcentage
# plutôt qu'un chiffre faux — les tokens et le coût restent affichés.
#
# Clé = identifiant complet `provider/model` (jamais le seul nom du modèle :
# deux fournisseurs peuvent proposer des variantes homonymes de limites
# différentes). Commenter/ignorer une clé est sans effet.
#
# Entretien : ajouter une entrée quand un nouveau modèle apparaît. Le
# `source` et le `verified_at` servent à tracer d'où vient la valeur.
"""

def _variant_sort_key(v: str):
    v = str(v).lower()
    return (0, _REASONING_SCALE.index(v), "") if v in _REASONING_SCALE else (1, 0, v)


def _provider_catalog() -> list:
    """Catalogue OpenCode : providers connectés avec leurs modèles.

    Sayrhazi n'invente rien : il relaie GET /config/providers et ne fait que
    filtrer l'affichage - statut « deprecated » exclu, modèles « free »
    (provider opencode, coût d'entrée nul) signalés, sections avec opencode
    d'abord (comme la TUI), modèles free d'abord puis date de sortie
    décroissante. Le cache dure 60 s pour ne pas charger la conversation.
    """
    global _PROVIDER_CATALOG_CACHE, _PROVIDER_CATALOG_AT
    import time
    now = time.time()
    if _PROVIDER_CATALOG_CACHE is not None and now - _PROVIDER_CATALOG_AT < _PROVIDER_CATALOG_TTL:
        return _PROVIDER_CATALOG_CACHE
    out = []
    try:
        req = urllib.request.Request("http://127.0.0.1:4096/config/providers", method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        _PROVIDER_CATALOG_CACHE = []
        _PROVIDER_CATALOG_AT = now
        return []
    providers = [p for p in (data.get("providers") or []) if isinstance(p, dict)]
    providers.sort(key=lambda p: (0 if p.get("id") == "opencode" else 1, str(p.get("name") or p.get("id") or "").lower()))
    for p in providers:
        pid = str(p.get("id") or "")
        if not pid:
            continue
        name = str(p.get("name") or pid)
        models_out = []
        for mid, m in (p.get("models") or {}).items():
            if not isinstance(m, dict):
                continue
            if str(m.get("status") or "active") == "deprecated":
                continue
            cost = m.get("cost") or {}
            try:
                input_cost = float(cost.get("input") or 0)
            except (TypeError, ValueError):
                input_cost = 0.0
            free = pid == "opencode" and input_cost == 0.0
            try:
                release = int(str(m.get("release_date") or "0").replace("-", "") or 0)
            except (TypeError, ValueError):
                release = 0
            try:
                context = int((m.get("limit") or {}).get("context") or 0)
            except (TypeError, ValueError):
                context = 0
            raw_variants = m.get("variants") if isinstance(m.get("variants"), dict) else {}
            variants = sorted(raw_variants.keys(), key=_variant_sort_key)
            models_out.append({
                "id": str(mid),
                "name": str(m.get("name") or mid),
                "free": bool(free),
                "releaseDate": release,
                "context": context,
                "variants": [str(v) for v in variants],
                # Relais capabilities OpenCode pour la tooltip CSS par item.
                # Ne jamais inventer de valeurs : on transfère telles quelles.
                "capabilities": m.get("capabilities") if isinstance(m.get("capabilities"), dict) else None,
                "modalities": m.get("modalities") if isinstance(m.get("modalities"), dict) else None,
            })
        # Comme la TUI : free d'abord, puis date de sortie décroissante, puis nom.
        models_out.sort(key=lambda m: (not m["free"], -m["releaseDate"], m["name"].lower()))
        if models_out:
            out.append({"id": pid, "name": name, "models": models_out})
    _PROVIDER_CATALOG_CACHE = out
    _PROVIDER_CATALOG_AT = now
    return out


def _parse_models_registry_file(path: Path) -> dict:
    """Parse `models:` d'un fichier registre (miroir du chargeur
    `load_model_limits` : cle aux 2e indent, context/source/verified_at au 4e).
    Retourne {cle: {"context_tokens": int|None, "source": str, "verified_at": str}}.
    """
    entries: dict = {}
    if not path.is_file():
        return entries
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return entries
    current: Optional[str] = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip() if not raw.strip().startswith("#") else ""
        if not line.strip():
            continue
        match = re.match(r"^\s{2}(\S+):\s*$", line)
        if match:
            current = match.group(1).strip().strip('"').strip("'")
            entries.setdefault(current, {})
            continue
        if current is None:
            continue
        match = re.match(r"^\s+(context_tokens|source|verified_at)\s*:\s*(.*)$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip().strip('"').strip("'")
        if key == "context_tokens":
            try:
                value = int(value)
            except (TypeError, ValueError):
                value = 0
        entries[current][key] = value
    return entries


def sync_models_registry(root: Path, attempts: int = 3) -> int:
    """Aligne .opencode/models.yaml sur le catalogue OpenCode actuel.

    Le registre ne contient que les modèles actuellement exposés par OpenCode
    et pourvus d'une fenêtre de contexte. L'en-tête et l'ordre Opencode FREE
    sont reproduits à chaque écriture ; les entrées absentes du catalogue sont
    supprimées. `source` et `verified_at` des entrées présentes sont conservés.
    """
    global _PROVIDER_CATALOG_CACHE
    catalog = None
    for _ in range(attempts):
        _PROVIDER_CATALOG_CACHE = None
        catalog = _provider_catalog()
        if catalog:
            break
        time.sleep(1)
    if not catalog:
        return 0

    path = root / ".opencode" / "models.yaml"
    existing = _parse_models_registry_file(path)
    today = time.strftime("%Y-%m-%d")
    entries: dict = {}
    metadata: dict = {}
    changed = 0
    for provider in catalog:
        pid = str(provider.get("id") or "")
        if not pid:
            continue
        provider_name = str(provider.get("name") or pid)
        for m in provider.get("models") or []:
            if not isinstance(m, dict):
                continue
            try:
                context = int(m.get("context") or 0)
            except (TypeError, ValueError):
                context = 0
            if context <= 0:
                continue
            key = f"{pid}/{str(m.get('id') or '')}"
            metadata[key] = {
                "free": bool(m.get("free")),
                "name": str(m.get("name") or m.get("id") or key),
                "provider": provider_name,
                "release": int(m.get("releaseDate") or 0),
            }
            previous = existing.get(key)
            entry = dict(previous) if isinstance(previous, dict) else {}
            if entry.get("context_tokens") != context:
                entry["context_tokens"] = context
                entry["verified_at"] = today
                changed += 1
            entry.setdefault("source", "relevé automatique par Sayrhazi via OpenCode (/config/providers)")
            entry.setdefault("verified_at", today)
            entries[key] = entry

    changed += len(set(existing) - set(entries))

    lines = MODELS_REGISTRY_HEADER.splitlines() + ["", "models:"]
    for key in sorted(entries, key=lambda value: (
        0 if value.startswith("opencode/") and metadata[value]["free"] else 1,
        0 if value.startswith("opencode/") else 1,
        metadata[value]["provider"].lower(),
        -metadata[value]["release"],
        metadata[value]["name"].lower(),
        value,
    )):
        entry = entries[key]
        lines.append(f"  {key}:")
        lines.append(f"    context_tokens: {int(entry['context_tokens'])}")
        if entry.get("source"):
            lines.append(f"    source: {entry['source']}")
        if entry.get("verified_at"):
            lines.append(f"    verified_at: \"{entry['verified_at']}\"")
    desired = "\n".join(lines) + "\n"

    try:
        current = path.read_text(encoding="utf-8")
    except OSError:
        current = ""
    if current == desired:
        return changed
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(desired, encoding="utf-8")
    except OSError:
        return changed
    return max(changed, 1)


def _provider_name(provider_id: str) -> str:
    pid = str(provider_id or "")
    if not pid:
        return ""
    for p in _provider_catalog():
        if p["id"] == pid:
            return p["name"]
    return pid


def _model_by_id(provider_id: str, model_id: str) -> Optional[dict]:
    for p in _provider_catalog():
        if p["id"] != provider_id:
            continue
        for m in p["models"]:
            if m["id"] == model_id:
                return m
    return None


def _format_ms(ms) -> str:
    """Formate une duree en ms, exactement comme OpenCode :

    < 1s   -> "12ms"
    < 60s  -> "2.8s"
    < 1h   -> "1m 23s"
    < 24h  -> "1h 5m"
    sinon  -> "2d 3h"
    """
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        return ""
    if ms < 0:
        return ""
    if ms < 1000:
        return f"{ms}ms"
    if ms < 60000:
        return f"{ms / 1000.0:.1f}s"
    if ms < 3600000:
        minutes = ms // 60000
        seconds = (ms % 60000) // 1000
        return f"{minutes}m {seconds}s"
    if ms < 86400000:
        hours = ms // 3600000
        minutes = (ms % 3600000) // 60000
        return f"{hours}h {minutes}m"
    days = ms // 86400000
    hours = (ms % 86400000) // 3600000
    return f"{days}d {hours}h"


def _format_duration(payload):
    """Calcule la duree d'un message/outil OpenCode (time.start -> time.end).

    Retourne "12ms" en dessous de la seconde, "2.8s" au-dela (comme la TUI).
    """
    if not isinstance(payload, dict):
        return ""
    t = payload.get("time") or {}
    start = t.get("start")
    end = t.get("end")
    if start and end:
        try:
            return _format_ms(int(end) - int(start))
        except (TypeError, ValueError):
            pass
    return ""


# Texte libere injecte par OpenCode dans le message utilisateur synthetique
# cree quand l'utilisateur execute un outil depuis l'UI.
_SYNTHETIC_USER_TEXT = "The following tool was executed by the user"


def _is_synthetic_user_message(role, text, parts):
    """Reproduit Mo() d'OpenCode : message utilisateur synthetique.

    role == "user", texte vide, aucune part, et une part de type "text"
    marquee synthetic dont le contenu est le libelle OpenCode.
    """
    if role != "user":
        return False
    if (text or "").strip():
        return False
    if parts:
        return False
    return True


def _has_synthetic_user_part(parts):
    """Vrai si une part de type "text" est marquee synthetic avec le libelle OpenCode."""
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        if str(part.get("type") or "") != "text":
            continue
        if part.get("synthetic") and str(part.get("text") or "") == _SYNTHETIC_USER_TEXT:
            return True
    return False


def _has_bash_tool(parts):
    """Vrai si une part contient un tool bash (filtre To() d'OpenCode)."""
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        if str(part.get("type") or "") != "tool":
            continue
        if str(part.get("tool") or "") == "bash":
            return True
    return False


def _turn_duration_ms(assistant_messages, parent_id, user_created):
    """Duree du tour affichee par la TUI (en ms).

    La TUI n'affiche pas la duree propre du message mais la duree du TOUR :
      - client web OpenCode : Mt() = max(time.completed des messages assistant
        du tour) - time.created du message utilisateur du tour ;
      - TUI terminal : Date.now() a la fin du tour - Date.now() a l'envoi du
        prompt (donc du message utilisateur).

    Retourne None si le debut du tour est inconnu (message utilisateur absent)
    ou si le tour n'a pas de fin : l'appelant garde alors la duree du message.
    """
    end = None
    for m in assistant_messages:
        if (m.get("role") or "") != "agent":
            continue
        if (m.get("parent_id") or "") != parent_id:
            continue
        m_end = m.get("_end")
        if isinstance(m_end, int) and (end is None or m_end > end):
            end = m_end
    if not isinstance(user_created, int) or end is None:
        return None
    if end < user_created:
        return None
    return end - user_created


def _part_to_item(payload, ctx, part_id=""):
    """Convertit un part OpenCode en item du tableau « items » d'un message.

    `ctx` est un dict mis à jour en place (créé par l'appelant pour chaque
    message) :

      {"pending_reasoning": None|dict,   # raisonnement vide en attente d'un texte
       "items": [], "tools_list": [],    # éléments structurés du message
       "raw_parts": [], "text_parts": [], # parts bruts et textes d'affichage
       "part_starts": [], "part_ends": []} # bornes time.start/end des parts

    Chaque item porte `part_id` (identifiant du part) : le frontend stocke les
    items en mémoire et les remplace en place quand l'agent streame.
    Retourne l'item créé (kind "reasoning"|"text"|"tool"), ou None quand le
    part ne produit pas d'item (step-start / step-finish).
    """
    ptype = str(payload.get("type") or "")
    ptext = str(payload.get("text") or "")

    # OpenCode stocke les données de l'outil (status, input, output,
    # metadata, title, time) dans le sous-objet `state` du part.
    ctx["raw_parts"].append({
        "type": ptype,
        "tool": str(payload.get("tool") or payload.get("tool_name") or ""),
        "text": ptext,
        "synthetic": bool(payload.get("synthetic")),
    })
    if ptype in ("tool", "file"):
        root_call_id = str(payload.get("callID") or "")
        root_tool = str(payload.get("tool") or payload.get("tool_name") or "")
        state = payload.get("state")
        if isinstance(state, dict):
            merged = dict(state)
            merged["type"] = ptype
            if root_tool:
                merged["tool"] = root_tool
            payload = merged

    # Durée du message : borne des parts (message.data n'a pas de time)
    tinfo = payload.get("time") or {}
    if isinstance(tinfo, dict):
        if tinfo.get("start"):
            ctx["part_starts"].append(tinfo.get("start"))
        if tinfo.get("end"):
            ctx["part_ends"].append(tinfo.get("end"))

    if ptype == "reasoning":
        # Un raisonnement vide annonce que le "Thought" de l'agent est
        # dans le part text suivant (modèles sans raisonnement natif).
        ctx["pending_reasoning"] = {
            "duration": _format_duration(payload),
        }
        if ptext:
            item = {
                "kind": "reasoning",
                "text": ptext,
                "duration": ctx["pending_reasoning"]["duration"],
                "native": True,
            }
            item["part_id"] = part_id
            ctx["items"].append(item)
            ctx["pending_reasoning"] = None
            return item
        return None

    if ptype in ("step-start", "step-finish"):
        return None

    if ptype == "retry":
        # RetryPart : le TUI l'affiche en "retrying in Xs" sans clôturer la boucle.
        r_err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        r_data = r_err.get("data") if isinstance(r_err.get("data"), dict) else {}
        r_time = payload.get("time") if isinstance(payload.get("time"), dict) else {}
        item = {
            "kind": "retry",
            "attempt": int(payload.get("attempt") or 0),
            "time_created": r_time.get("created"),
            "error_name": str(r_err.get("name") or "UnknownError"),
            "error_status": r_data.get("statusCode"),
            "error_message": str(r_data.get("message") or ""),
            "error_is_retryable": bool(r_data.get("isRetryable", False)),
            "error_response_body": str(r_data.get("responseBody") or ""),
        }
        item["part_id"] = part_id
        ctx["items"].append(item)
        return item

    if ptype == "text" and ptext:
        if ctx["pending_reasoning"] is not None:
            # C'est le "Thought" de l'agent : le raisonnement est vide,
            # ce texte est un preambule (naturel = False).
            item = {
                "kind": "reasoning",
                "text": ptext,
                "duration": ctx["pending_reasoning"]["duration"],
                "native": False,
            }
            item["part_id"] = part_id
            ctx["items"].append(item)
            ctx["pending_reasoning"] = None
            return item
        item = {"kind": "text", "text": ptext}
        item["part_id"] = part_id
        ctx["items"].append(item)
        ctx["text_parts"].append(ptext)
        return item

    if ptype in ("tool", "file"):
        # Un outil in invalide le "Thought" en attente : le texte qui suivra
        # est la reponse finale de l'agent, pas un raisonnement.
        ctx["pending_reasoning"] = None
        tool_name = str(payload.get("tool") or ptype.upper())
        file_path = str(payload.get("filename") or payload.get("file") or payload.get("file_path") or payload.get("path") or "")
        file_url = str(payload.get("url") or "")
        file_mime = str(payload.get("mime") or "")
        call_state = str(payload.get("status") or "")
        call_id = str(payload.get("callID") or "")

        # Données de diff pour Edit/Write/Patch (OpenCode les stocke dans input / metadata)
        input_data = payload.get("input") or {}
        if isinstance(input_data, dict):
            old_string = str(input_data.get("oldString") or "")
            # Pour un Write, le contenu est sous 'content' (pas 'newString')
            new_string = str(input_data.get("newString") or input_data.get("content") or "")
            if not file_path:
                file_path = str(
                    input_data.get("filePath")
                    or input_data.get("file")
                    or input_data.get("file_path")
                    or input_data.get("path")
                    or ""
                )
        else:
            old_string = ""
            new_string = ""

        if not file_path:
            file_path = str(
                payload.get("filePath")
                or payload.get("fileName")
                or payload.get("file_name")
                or payload.get("target")
                or ""
            )

        metadata = payload.get("metadata") or {}
        if isinstance(metadata, dict):
            diff_content = str(metadata.get("diff") or "")
            filediff = metadata.get("filediff") or {}
            if isinstance(filediff, dict):
                patch_content = str(filediff.get("patch") or diff_content)
                additions = filediff.get("additions")
                deletions = filediff.get("deletions")
            else:
                patch_content = diff_content
                additions = None
                deletions = None
        else:
            diff_content = ""
            patch_content = diff_content
            additions = None
            deletions = None

        if not patch_content and old_string and new_string:
            patch_content = _build_unified_diff(file_path, old_string, new_string)

        # Construire un texte descriptif pour l'affichage outil
        if ptype == "tool":
            desc_parts = [tool_name]
            if file_path:
                desc_parts.append(file_path)
            if call_state:
                desc_parts.append("[" + call_state + "]")
            if call_id:
                desc_parts.append("(" + call_id + ")")
            ptext = " ".join(desc_parts)
        elif ptype == "file":
            desc_parts = [file_path or file_url]
            if file_mime:
                desc_parts.append("(" + file_mime + ")")
            ptext = " ".join(desc_parts) or "(fichier)"

        # Contenu specifique pour Bash (commande + resultat)
        tool_content = ""
        todos_list = None
        if tool_name.lower() == "bash":
            cmd = str(input_data.get("command") or "") if isinstance(input_data, dict) else ""
            output = str(payload.get("output") or "")
            parts = []
            if cmd:
                parts.append("$ " + cmd)
            if output:
                parts.append(output)
            tool_content = "\n".join(parts)
        elif tool_name.lower() in ("grep", "glob"):
            # Outils de recherche : on affiche le motif et les filtres
            lines = []
            if isinstance(input_data, dict):
                pat = str(input_data.get("pattern") or "")
                pth = str(input_data.get("path") or "")
                inc = str(input_data.get("include") or "")
                mode = str(input_data.get("output_mode") or "")
                if pat:
                    lines.append("Motif : " + pat)
                if inc:
                    lines.append("Filtre : " + inc)
                if mode:
                    lines.append("Affichage : " + mode)
                if pth and not file_path:
                    file_path = pth
            tool_content = "\n".join(lines)
        elif tool_name.lower().startswith("playwright") or tool_name.lower() not in (
            "read", "edit", "write", "patch", "grep", "glob", "list", "lsp", "bash",
        ):
            # Outils type Playwright : paramètres d'appel + résultat
            chunks = []
            if isinstance(input_data, dict) and input_data:
                try:
                    chunks.append("Parametres :\n" + json.dumps(input_data, ensure_ascii=False, indent=2))
                except (TypeError, ValueError):
                    chunks.append("Parametres :\n" + str(input_data))
            output = str(payload.get("output") or "")
            if output.strip():
                chunks.append("Resultat :\n" + output)
            tool_content = "\n\n".join(chunks)
            if len(tool_content) > 4000:
                tool_content = tool_content[:4000] + "\n... (tronque)"

        # Liste de todos (outil Todowrite) : rendu dédié
        if tool_name.lower() in ("todowrite", "todo_write", "todo"):
            raw_todos = input_data.get("todos") if isinstance(input_data, dict) else None
            if isinstance(raw_todos, list):
                todos_list = []
                for item in raw_todos:
                    if isinstance(item, dict):
                        todos_list.append({
                            "content": str(item.get("content") or ""),
                            "status": str(item.get("status") or "pending"),
                            "priority": str(item.get("priority") or ""),
                        })

        # Parametres d'appel affiches entre crochets (comme la TUI) :
        # read [offset=30, limit=40], grep [pattern=..., include=...]
        params = []
        if isinstance(input_data, dict):
            for key in ("offset", "limit"):
                if input_data.get(key) is not None:
                    params.append(f"{key}={input_data.get(key)}")
            for key in ("pattern", "include", "output_mode", "path"):
                val = input_data.get(key)
                if val:
                    params.append(f"{key}={val}" if key != "pattern" else f"pattern={val}")
        params_text = ", ".join(params)

        source_path = ""
        source_obj = payload.get("source")
        if isinstance(source_obj, dict):
            source_path = str(source_obj.get("path") or "")

        tool_item = {
            "kind": "tool",
            "tool": tool_name,
            "file": file_path,
            "directory": bool(payload.get("directory")),
            "source_path": source_path,
            "params": params_text,
            "url": file_url,
            "mime": file_mime,
            "todos": todos_list,
            "state": call_state,
            "call_id": call_id,
            "old_content": old_string,
            "new_content": new_string,
            "diff_content": patch_content,
            "tool_content": tool_content,
            "additions": additions,
            "deletions": deletions,
            "error": str(payload.get("error") or ""),
            "duration": _format_duration(payload),
        }
        tool_item["part_id"] = part_id
        ctx["items"].append(tool_item)
        ctx["tools_list"].append(tool_item)
        return tool_item

    return None


def _unified_diff_from_pairs(file_path, old_string, new_string):
    return _build_unified_diff(file_path, old_string, new_string)


def _select_assistant_footers(messages, synthetic_user_ids=None):
    """Reproduit ko()/To()/Mo() d'OpenCode 1.19.0 pour poser les footers agent.

    Ordre des messages (extrait du binaire opencode 1.19.0) :
      - le store classe les messages par (time.created, id) : le comparateur
        `ua` compare `time.created + id`, la fusion `b$()` trie toujours la
        liste (`compare: ua`) et un message recu en direct est insere a la
        position triee (recherche binaire sur `Jc`) ;
      - la liste n'est donc JAMAIS anti-chronologique : ko() la parcourt a
        l'envers et retient le message le PLUS RECENT de chaque tour
        (parentID), en streaming comme apres rechargement de session.

    Duree : la TUI affiche la duree du TOUR (voir _turn_duration_ms), pas la
    duree propre du message.

    Le caller doit fournir synthetic_user_ids (ids des messages utilisateur
    synthetiques, cf. Mo()).
    """
    synthetic_user_ids = synthetic_user_ids or set()

    # Ordre natif du store : (time.created, id).
    ordered = sorted(
        messages,
        key=lambda m: (m.get("_start") or 0, str(m.get("_msg_id") or "")),
    )

    # Debut de chaque tour : time.created du message utilisateur (le parent).
    user_created = {}
    for m in ordered:
        if (m.get("role") or "") != "user":
            continue
        mid = str(m.get("_msg_id") or "")
        start = m.get("_start")
        if mid and isinstance(start, int):
            user_created[mid] = start

    seen_parents = set()
    for m in reversed(ordered):
        if (m.get("role") or "") != "agent":
            continue
        # To() : parent synthetique + appel bash
        if (m.get("parent_id") or "") in synthetic_user_ids and _has_bash_tool(m.get("raw_parts") or []):
            continue
        pid = m.get("parent_id") or ""
        if pid in seen_parents:
            continue
        seen_parents.add(pid)
        start = m.get("_start")
        end = m.get("_end")
        if not isinstance(end, int) or not start or end <= start:
            continue
        m["series_end"] = True
        turn_ms = _turn_duration_ms(ordered, pid, user_created.get(pid))
        m["duration"] = _format_ms(turn_ms if turn_ms is not None else end - start)


def _format_time_ms(value) -> str:
    """Formate un timestamp OpenCode (ms) en HH:MM, ou vide si invalide."""
    try:
        from datetime import datetime
        ts = int(value) / 1000.0
        return datetime.fromtimestamp(ts).strftime("%H:%M")
    except (TypeError, ValueError, OSError):
        return str(value or "")


def _translate_opencode_event(event, session_id):
    """Normalise un événement SSE OpenCode 1.19 pour le dashboard.

    Retourne None si l'événement ne concerne pas `session_id` ou n'est pas
    utile. Les événements de part sont traduits dans la même structure que
    celle produite par /api/chat/session/:id/messages, pour permettre au
    frontend de remplacer un item en place sans rechargement.
    """
    event_type = event.get("type") or ""
    props = event.get("properties") or {}
    if props.get("sessionID") != session_id:
        return None
    if event_type == "session.idle":
        # Fin du tour : le message agent est complet
        return {"kind": "idle", "sessionID": session_id}
    if event_type == "session.status":
        status = props.get("status") or {}
        stype = str(status.get("type") or "")
        if stype == "busy":
            return {"kind": "busy", "sessionID": session_id}
        if stype == "idle":
            return {"kind": "idle", "sessionID": session_id}
        if stype == "retry":
            # OpenCode retry inner-service: jamais d'erreur, juste ce broadcast.
            return {"kind": "retry", "sessionID": session_id,
                    "attempt": int(status.get("attempt") or 0),
                    "message": str(status.get("message") or ""),
                    "next": int(status.get("next") or 0)}
    if event_type == "session.error":
        err = props.get("error") or {}
        data = err.get("data") if isinstance(err.get("data"), dict) else {}
        return {"kind": "error", "sessionID": session_id,
                "name": str(err.get("name") or "UnknownError"),
                "statusCode": data.get("statusCode"),
                "message": str(data.get("message") or err.get("message") or err.get("name") or ""),
                "isRetryable": bool(data.get("isRetryable", False)),
                "responseBody": str(data.get("responseBody") or "")}
    if event_type == "message.updated":
        info = props.get("info") or {}
        completed = (info.get("time") or {}).get("completed")
        out = {"kind": "message", "messageID": str(info.get("id") or ""),
               "role": str(info.get("role") or ""),
               "completed": completed if isinstance(completed, int) else None,
               "agent": str(info.get("agent") or info.get("mode") or "")}
        err_info = info.get("error") if isinstance(info.get("error"), dict) else None
        if err_info:
            err_data = err_info.get("data") if isinstance(err_info.get("data"), dict) else {}
            out["error"] = {"name": str(err_info.get("name") or "UnknownError"),
                             "statusCode": err_data.get("statusCode"),
                             "message": str(err_data.get("message") or ""),
                             "isRetryable": bool(err_data.get("isRetryable", False)),
                             "responseBody": str(err_data.get("responseBody") or "")}
        return out
    if event_type == "message.part.delta":
        # delta incrémental — tout champ compte (text, reasoning, etc.) :
        # c'est le premier signe que l'agent a réellement commencé à agir.
        field = str(props.get("field") or "")
        delta = str(props.get("delta") or "")
        if not delta:
            return None
        return {
            "kind": "text_delta",
            "messageID": str(props.get("messageID") or ""),
            "partID": str(props.get("partID") or ""),
            "delta": delta,
        }
    if event_type == "message.part.updated":
        part = props.get("part") or {}
        part_id = str(part.get("id") or "")
        ctx = {"pending_reasoning": None,
               "items": [], "tools_list": [],
               "raw_parts": [], "text_parts": [],
               "part_starts": [], "part_ends": []}
        item = _part_to_item(part, ctx, part_id)
        if item is None:
            return None
        return {
            "kind": "part",
            "messageID": str(part.get("messageID") or ""),
            "partID": part_id,
            "item": item,
        }
    return None


def _fresh_part_ctx():
    return {"pending_reasoning": None,
            "items": [], "tools_list": [],
            "raw_parts": [], "text_parts": [],
            "part_starts": [], "part_ends": []}


def _build_unified_diff(file_path: str, old_string: str, new_string: str) -> str:
    """Construit un patch unifié à partir de deux chaînes (stdlib difflib)."""
    old_lines = old_string.splitlines(keepends=True)
    new_lines = new_string.splitlines(keepends=True)
    if not old_lines:
        old_lines = [""]
    if not new_lines:
        new_lines = [""]
    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile="a/" + file_path,
        tofile="b/" + file_path,
        lineterm="",
    ))
    return "\n".join(diff)


# Instance unique du moteur, partagée avec le handler HTTP et le signal handler.
watcher_engine: Optional[WatcherEngine] = None
_http_server: Optional[HTTPServer] = None


class ServerHandler(_mod.Handler):
    """Handler du dashboard (base : sayrhazi-modules.Handler) étendu avec l'API de contrôle du watcher."""

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
            # Connexion coupée brutalement par le client (onglet fermé, SSE arrêté, etc.)
            # sous Windows : on marque la connexion pour fermeture sans faire planter
            # la boucle serve_forever.
            self.close_connection = True

    def _send_redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def _send_index_with_root(self) -> None:
        """Sert index.html avec window.__sayrhaziRoot injecté (racine du projet)."""
        root_str = str(ServerHandler.project_root)
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        injection = '<script>window.__sayrhaziRoot = ' + json.dumps(root_str, ensure_ascii=False) + ';</script>'
        marker = '<script src="/sayrhazi-server.js"></script>'
        if marker in html:
            html = html.replace(marker, injection + marker, 1)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def do_GET(self):
        # Routes API
        if self.path == "/api/worker/status":
            self._send_json(watcher_engine.status() if watcher_engine else {"running": False, "pid": os.getpid()})
            return
        if self.path == "/api/watcher-logs":
            logs = watcher_engine.get_logs() if watcher_engine else []
            self._send_json({"logs": logs})
            return
        if self.path == "/api/chat/agents":
            self._handle_chat_agents()
            return
        if self.path.startswith("/api/chat/history/"):
            agent_file = self.path.split("/api/chat/history/", 1)[1]
            self._handle_chat_history(agent_file)
            return
        if self.path.startswith("/api/chat/metrics/"):
            agent_file = self.path.split("/api/chat/metrics/", 1)[1]
            self._handle_chat_metrics(agent_file)
            return
        if self.path.startswith("/api/chat/stream"):
            self._handle_chat_session_stream()
            return
        if self.path == "/api/chat/session/event":
            self._handle_chat_session_event_sse()
            return
        if self.path.startswith("/api/chat/session/"):
            parts = self.path.split("/")
            # parts = ['', 'api', 'chat', 'session', session_id, 'messages']
            if len(parts) >= 6 and parts[5] == "messages":
                session_id = parts[4]
                self._handle_chat_session_messages(session_id)
                return
            if len(parts) >= 6 and parts[5] == "info":
                session_id = parts[4]
                self._handle_chat_session_info(session_id)
                return
            if len(parts) >= 6 and parts[5] == "pending":
                session_id = parts[4]
                self._handle_chat_pending(session_id)
                return
            else:
                self._handle_chat_session_get()
                return
        if self.path.startswith("/api/chat/find-file"):
            self._handle_chat_find_file()
            return
        if self.path == "/api/chat/providers":
            self._send_json({"ok": True, "providers": _provider_catalog()})
            return
        if self.path.startswith("/api/chat/current-model"):
            self._handle_chat_current_model()
            return
        if self.path == "/api/chat/event":
            self._handle_chat_event()
            return
        if self.path.startswith("/api/chat/global/event"):
            self._handle_chat_global_event_sse()
            return
        if self.path == "/api/chat/debug":
            self._handle_chat_debug()
            return

        # Routes de pages (SPA : même index.html pour tout, le JS choisit la vue)
        if self.path == "/":
            self._send_redirect("/dashboard")
            return
        if self.path in ("/dashboard", "/chat") or self.path.startswith("/agents/") or self.path.startswith("/chat/"):
            self._send_index_with_root()
            return
        if self.path == "/index.html":
            self._send_index_with_root()
            return

        # Fichiers statiques et autres (déléguer à sayrhazi-modules.Handler)
        if self.path == "/sayrhazi-server.js":
            self._send_file(STATIC_DIR / "sayrhazi-server.js", "application/javascript")
            return
        if self.path == "/diff-view.js":
            self._send_file(STATIC_DIR / "diff-view.js", "application/javascript")
            return
        if self.path == "/diff.esm.js":
            self._send_file(STATIC_DIR / "diff.esm.js", "text/javascript")
            return
        if self.path == "/diff.js":
            self._send_file(NODE_MODULES_DIR / "diff" / "dist" / "diff.js", "application/javascript")
            return
        if self.path == "/marked.min.js":
            self._send_file(STATIC_DIR / "marked.min.js", "application/javascript")
            return
        if self.path == "/purify.min.js":
            self._send_file(STATIC_DIR / "purify.min.js", "application/javascript")
            return
        if self.path == "/highlight.min.js":
            self._send_file(STATIC_DIR / "highlight.min.js", "application/javascript")
            return
        if self.path == "/hljs.bundle.js":
            self._send_file(STATIC_DIR / "hljs.bundle.js", "application/javascript")
            return
        super().do_GET()

    def do_POST(self):
        if self.path == "/api/worker/start":
            self._handle_worker_start()
        elif self.path == "/api/worker/stop":
            self._handle_worker_stop()
        elif self.path == "/api/chat/send":
            self._handle_chat_send()
        elif self.path == "/api/chat/session":
            self._handle_chat_session_create()
        elif self.path.startswith("/api/chat/session/") and self.path.endswith("/message"):
            self._handle_chat_session_post()
        elif self.path.startswith("/api/chat/session/") and self.path.endswith("/prompt_async"):
            self._handle_chat_session_prompt_async()
        elif self.path.startswith("/api/chat/session/") and self.path.endswith("/revert/preview"):
            self._handle_chat_session_revert_preview()
        elif self.path.startswith("/api/chat/session/") and self.path.endswith("/revert"):
            self._handle_chat_session_revert()
        elif self.path.startswith("/api/chat/session/") and self.path.endswith("/unrevert"):
            self._handle_chat_session_unrevert()
        elif self.path.startswith("/api/chat/session/") and self.path.endswith("/abort"):
            self._handle_chat_session_abort()
        elif self.path.startswith("/api/chat/session/") and "/permission/" in self.path and self.path.endswith("/reply"):
            self._handle_chat_permission_reply()
        elif self.path.startswith("/api/chat/session/") and "/question/" in self.path and self.path.endswith("/reply"):
            self._handle_chat_question_reply()
        elif self.path.startswith("/api/chat/session/") and "/question/" in self.path and self.path.endswith("/reject"):
            self._handle_chat_question_reject()
        elif self.path.startswith("/api/chat/session/") and self.path.endswith("/title"):
            self._handle_chat_session_title()
        else:
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self):
        if self.path.startswith("/api/chat/session/"):
            parts = self.path.split("/")
            if len(parts) >= 5:
                self._handle_chat_session_delete(parts[4])
                return
        self.send_response(404)
        self.end_headers()

    def _send_json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        if length > 0:
            return self.rfile.read(length)
        return b""

    def _handle_worker_start(self) -> None:
        self._read_body()
        if watcher_engine is None:
            self._send_json({"ok": False, "message": "moteur non initialisé"}, status=500)
            return
        if watcher_engine.start(ServerHandler.engine_config, ServerHandler.engine_usable):
            self._send_json({"ok": True, "message": "watcher démarré"})
        else:
            self._send_json({"ok": False, "message": "watcher déjà en cours"}, status=409)

    def _handle_worker_stop(self) -> None:
        self._read_body()
        if watcher_engine is None:
            self._send_json({"ok": False, "message": "moteur non initialisé"}, status=500)
            return
        if watcher_engine.stop():
            self._send_json({"ok": True, "message": "watcher arrêté"})
        else:
            self._send_json({"ok": False, "message": "watcher déjà arrêté"}, status=409)

    def _handle_chat_agents(self) -> None:
        """GET /api/chat/agents : liste des agents disponibles pour le chat."""
        agents = []
        for role, agent_file in ROLE_AGENT_FILE_WW.items():
            agents.append({
                "agent_file": agent_file,
                "role": role,
                "display_name": _chat_agent_display_name(agent_file),
                "report": ROLE_REPORT_WW.get(agent_file, agent_file + ".txt"),
            })
        self._send_json({"agents": agents})

    def _handle_chat_history(self, agent_file: str) -> None:
        """GET /api/chat/history/:agent : historique des conversations."""
        root = ServerHandler.project_root
        messages = get_chat_history(root, agent_file)
        self._send_json({"agent": agent_file, "messages": messages})

    def _handle_chat_metrics(self, agent_file: str) -> None:
        """GET /api/chat/metrics/:agent : métriques globales de l'agent."""
        root = ServerHandler.project_root
        try:
            db_path = opencode_db_path()
            if not db_path:
                self._send_json({"agent": agent_file, "sessions": 0, "total_cost": "n/d", "total_tokens": "n/d", "last_activity": "n/d"})
                return
            sessions = find_sessions(db_path, str(root), agent_file, prefix="")
            total_cost = 0.0
            total_tokens = 0
            last_activity = "n/d"
            last_time = 0
            for s in sessions:
                cost = s.get("cost") or 0.0
                try:
                    total_cost += float(cost)
                except (TypeError, ValueError):
                    pass
                ctx = s.get("context_tokens") or 0
                try:
                    total_tokens += int(ctx)
                except (TypeError, ValueError):
                    pass
                tu = s.get("time_updated") or 0
                if tu and tu > last_time:
                    last_time = tu
                    last_activity = str(s.get("time_updated") or "")
            self._send_json({
                "agent": agent_file,
                "sessions": len(sessions),
                "total_cost": f"${total_cost:.2f}",
                "total_tokens": f"{total_tokens:,}",
                "last_activity": last_activity,
            })
        except Exception:
            self._send_json({"agent": agent_file, "sessions": 0, "total_cost": "n/d", "total_tokens": "n/d", "last_activity": "n/d"})

    def _handle_chat_debug(self) -> None:
        """GET /api/chat/debug : debug des sessions OpenCode pour diagnostiquer."""
        root = ServerHandler.project_root
        try:
            db_path = opencode_db_path()
            db_info = str(db_path) if db_path else "introuvable"
            sessions_raw = []
            sessions_history = []
            if db_path:
                import sqlite3
                db = sqlite3.connect("file:" + str(db_path) + "?mode=ro", uri=True, timeout=5)
                try:
                    cur = db.cursor()
                    cur.execute("SELECT id, title, cost, time_created, time_updated, directory, agent FROM session ORDER BY time_updated DESC LIMIT 20")
                    sessions_raw = [dict(zip(("id", "title", "cost", "time_created", "time_updated", "directory", "agent"), row)) for row in cur.fetchall()]
                finally:
                    db.close()
                sessions = find_sessions(db_path, str(root), "lawibrahim", prefix="")
                for s in sessions:
                    sessions_history.append({
                        "id": s.get("id"),
                        "title": s.get("title"),
                        "cost": s.get("cost"),
                        "time_created": s.get("time_created"),
                        "time_updated": s.get("time_updated"),
                        "auto": s.get("auto"),
                    })
            history = get_chat_history(root, "lawibrahim")
            self._send_json({
                "db_path": db_info,
                "root": str(root),
                "sessions_raw": sessions_raw,
                "sessions_history": sessions_history,
                "history_count": len(history),
                "history": history[:5],
            })
        except Exception as e:
            self._send_json({"error": str(e)}, status=500)

    def _handle_chat_send(self) -> None:
        """POST /api/chat/send : envoyer un message à un agent via OpenCode Server."""
        body = self._read_body()
        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json({"ok": False, "message": "JSON invalide"}, status=400)
            return
        agent_file = data.get("agent", "")
        message = data.get("message", "")
        if not agent_file or not message:
            self._send_json({"ok": False, "message": "agent et message requis"}, status=400)
            return
        root = ServerHandler.project_root

        # Utiliser OpenCode Server si disponible
        global opencode_server
        if opencode_server is not None:
            # Créer une session native et utiliser son VRAI identifiant
            # (OpenCode renvoie ses_... ; ne jamais fabriquer d'identifiant).
            sc, session = opencode_server.create_session(agent_file, str(root))
            session_id = str(session.get("id") or "") if isinstance(session, dict) else ""
            if sc in (200, 201) and session_id:
                sc, result = opencode_server.send_message(session_id, message, agent_file, str(root))
                if sc in (200, 201):
                    self._send_json({"ok": True, "session_id": session_id, "response": result.get("message", ""), "timestamp": result.get("timestamp", ""), "error": result.get("error")})
                    return
            self._send_json({"ok": False, "message": "échec OpenCode Server", "error": str(result if isinstance(result, dict) else session)})
            return

        # Fallback : ancien mécanisme opencode run
        result = run_chat(root, agent_file, message)
        self._send_json(result)

    def _handle_chat_session_get(self) -> None:
        """GET /api/chat/session/:agent - créer une session OpenCode (route historique).

        La création se fait désormais par POST /api/chat/session ; cette route
        est conservée pour les appelants existants et délègue à la même
        création native (sans titre, pour laisser OpenCode renommer la
        discussion après le premier message).
        """
        parts = self.path.split("/")
        if len(parts) < 5:
            self._send_json({"ok": False, "message": "agent manquant"}, status=400)
            return
        agent_file = parts[4]
        root = ServerHandler.project_root
        global opencode_server
        if opencode_server is None:
            self._send_json({"ok": False, "message": "OpenCode Server non disponible"}, status=503)
            return
        sc, result = opencode_server.create_session(agent_file, str(root))
        self._send_json({"ok": sc in (200, 201), "session": result, "error": result.get("error") if sc not in (200, 201) else None}, status=sc if sc in (200, 201, 400, 500) else 500)

    def _handle_chat_session_create(self) -> None:
        """POST /api/chat/session - creation native d'une session OpenCode.

        Corps : {"agent": "ali"}. La session est creee par OpenCode dans le
        repertoire du projet, SANS titre : OpenCode renomme la session
        lui-meme apres le premier message (agent natif `title`).
        """
        body = self._read_body()
        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json({"ok": False, "message": "JSON invalide"}, status=400)
            return
        agent_file = str(data.get("agent") or "").strip()
        if not agent_file:
            self._send_json({"ok": False, "message": "agent requis"}, status=400)
            return
        root = ServerHandler.project_root
        global opencode_server
        if opencode_server is None:
            self._send_json({"ok": False, "message": "OpenCode Server non disponible"}, status=503)
            return
        sc, result = opencode_server.create_session(agent_file, str(root))
        if sc not in (200, 201):
            self._send_json({"ok": False, "message": "création de session impossible",
                             "error": result.get("error") or result.get("raw")}, status=500)
            return
        session_id = str(result.get("id") or "")
        if not session_id:
            self._send_json({"ok": False, "message": "OpenCode n'a pas renvoyé d'identifiant de session"},
                             status=500)
            return
        self._send_json({
            "ok": True,
            "session_id": session_id,
            "session": result,
            "title": str(result.get("title") or ""),
            "agent": str(result.get("agent") or agent_file),
        })

    def _handle_chat_session_delete(self, session_id: str) -> None:
        """DELETE /api/chat/session/:id - suppression native d'une session.

        La suppression passe par l'endpoint natif d'OpenCode (qui supprime
        aussi les sessions enfants). Sayrhazi ne touche jamais aux donnees
        OpenCode en base : il attend la confirmation du serveur avant de
        retirer la session de l'interface.
        """
        root = ServerHandler.project_root
        global opencode_server
        if opencode_server is None:
            self._send_json({"ok": False, "message": "OpenCode Server non disponible"}, status=503)
            return
        sc, result = opencode_server.delete_session(session_id, str(root))
        if sc not in (200, 201):
            self._send_json({"ok": False, "session_id": session_id,
                             "message": "suppression refusée par OpenCode",
                             "error": result.get("error") or result.get("raw")}, status=500)
            return
        self._send_json({"ok": True, "session_id": session_id, "deleted": result})

    def _handle_chat_session_info(self, session_id: str) -> None:
        """GET /api/chat/session/:id/info - état natif d'une session.

        Sert notamment à relire le titre après le renommage automatique
        d'OpenCode.
        """
        root = ServerHandler.project_root
        global opencode_server
        if opencode_server is None:
            self._send_json({"ok": False, "message": "OpenCode Server non disponible"}, status=503)
            return
        sc, result = opencode_server.get_session(session_id, str(root))
        if sc != 200:
            self._send_json({"ok": False, "session_id": session_id,
                             "error": result.get("error") or result.get("raw")}, status=404 if sc == 404 else 500)
            return

        # Usage du contexte (dernier message assistant), comme le tableau de bord.
        usage_pct = None
        usage_tokens = None
        usage_limit = None
        try:
            db_path = opencode_db_path()
            if db_path:
                payload = last_assistant_message(db_path, session_id)
                if payload:
                    tokens = message_context_tokens(payload)
                    model_key = message_model_key(payload)
                    limits = load_model_limits(root)
                    limit = limits.get(model_key) if isinstance(limits, dict) else None
                    if isinstance(limit, int) and limit > 0 and tokens > 0:
                        usage_pct = round(tokens / limit * 100, 1)
                        usage_tokens = tokens
                        usage_limit = limit
        except Exception:
            pass

        self._send_json({
            "ok": True,
            "session_id": str(result.get("id") or session_id),
            "title": str(result.get("title") or ""),
            "agent": str(result.get("agent") or ""),
            "parent_id": str(result.get("parentID") or ""),
            "directory": str(result.get("directory") or ""),
            "default_title": _is_default_session_title(str(result.get("title") or "")),
            "revert": result.get("revert") or None,
            "summary": result.get("summary") or None,
            "usage_pct": usage_pct,
            "usage_tokens": usage_tokens,
            "usage_limit": usage_limit,
        })

    def _session_id_from_path(self) -> str:
        parts = self.path.split("/")
        return str(parts[4]) if len(parts) >= 6 else ""

    def _handle_chat_session_revert(self) -> None:
        """POST /api/chat/session/:id/revert - revert natif d'OpenCode.

        Corps : {"messageID": "msg_…", "partID": "prt_…" (optionnel)}.
        Pass-through : Sayrhazi ne touche jamais à SQLite ni aux snapshots ;
        c'est OpenCode qui stocke session.revert et restaure les fichiers.
        La suppression effective des messages reverted aura lieu à l'envoi
        suivant (cleanup natif dans prompt_async).
        """
        session_id = self._session_id_from_path()
        if not session_id:
            self._send_json({"ok": False, "message": "session_id manquant"}, status=400)
            return
        body = self._read_body()
        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json({"ok": False, "message": "JSON invalide"}, status=400)
            return
        message_id = str(data.get("messageID") or data.get("message_id") or "").strip()
        if not message_id:
            self._send_json({"ok": False, "message": "messageID requis"}, status=400)
            return
        part_id = str(data.get("partID") or "").strip()
        root = ServerHandler.project_root
        global opencode_server
        if opencode_server is None:
            self._send_json({"ok": False, "message": "OpenCode Server non disponible"}, status=503)
            return
        sc, result = opencode_server.revert_session(session_id, message_id, str(root), part_id)
        if sc != 200:
            self._send_json({"ok": False, "session_id": session_id,
                             "message": "revert refusé par OpenCode",
                             "error": result.get("error") or result.get("raw") or result.get("message")},
                            status=sc if sc in (400, 404, 409) else 500)
            return
        self._send_json({
            "ok": True,
            "session_id": str(result.get("id") or session_id),
            "revert": result.get("revert") or None,
            "summary": result.get("summary") or None,
            "session": result,
        })

    def _handle_chat_session_revert_preview(self) -> None:
        """POST /api/chat/session/:id/revert/preview - aperçu sans effet.

        Sayrhazi n'envoie jamais le revert côté OpenCode avant l'envoi réel
        (les fichiers seraient sinon restaurés au clic « Restaurer ce
        message »). Pour afficher le bloc « fichiers concernés » sans effet
        de bord, Sayrhazi interroge ici un revert immédiat suivi d'un
        unrevert immédiat : OpenCode restaure les fichiers dans l'état
        pré-revert, calcule le diff, puis restaure l'état d'origine. Aucun
        marqueur n'est laissé (session.revert reste null) ; Sayrhazi ne le
        fusionne à la vraie question qu'au moment de l'envoi.
        """
        session_id = self._session_id_from_path()
        if not session_id:
            self._send_json({"ok": False, "message": "session_id manquant"}, status=400)
            return
        body = self._read_body()
        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json({"ok": False, "message": "JSON invalide"}, status=400)
            return
        message_id = str(data.get("messageID") or data.get("message_id") or "").strip()
        if not message_id:
            self._send_json({"ok": False, "message": "messageID requis"}, status=400)
            return
        part_id = str(data.get("partID") or "").strip()
        root = ServerHandler.project_root
        global opencode_server
        if opencode_server is None:
            self._send_json({"ok": False, "message": "OpenCode Server non disponible"}, status=503)
            return
        sc1, r1 = opencode_server.revert_session(session_id, message_id, str(root), part_id)
        if sc1 != 200:
            self._send_json({"ok": False, "session_id": session_id,
                             "message": "revert refusé par OpenCode",
                             "error": r1.get("error") or r1.get("raw") or r1.get("message")},
                            status=sc1 if sc1 in (400, 404, 409) else 500)
            return
        diff = (r1.get("revert") or {}).get("diff") if isinstance(r1, dict) else ""
        summary = r1.get("summary") if isinstance(r1, dict) else None
        sc2, r2 = opencode_server.unrevert_session(session_id, str(root))
        if sc2 != 200:
            self._send_json({"ok": False, "session_id": session_id,
                             "message": "aperçu revert : impossible de restaurer l'état d'origine",
                             "error": r2.get("error") or r2.get("raw") or r2.get("message")},
                            status=500)
            return
        self._send_json({
            "ok": True,
            "session_id": str(r1.get("id") or session_id),
            "diff": diff or "",
            "summary": summary or None,
        })


    def _handle_chat_session_unrevert(self) -> None:
        """POST /api/chat/session/:id/unrevert - annule un revert en attente.

        Restaure l'état d'avant le revert (fichiers + session.revert à null).
        Les messages reverted n'ont jamais été supprimés : ils réapparaissent.
        """
        session_id = self._session_id_from_path()
        if not session_id:
            self._send_json({"ok": False, "message": "session_id manquant"}, status=400)
            return
        root = ServerHandler.project_root
        global opencode_server
        if opencode_server is None:
            self._send_json({"ok": False, "message": "OpenCode Server non disponible"}, status=503)
            return
        sc, result = opencode_server.unrevert_session(session_id, str(root))
        if sc != 200:
            self._send_json({"ok": False, "session_id": session_id,
                             "message": "unrevert refusé par OpenCode",
                             "error": result.get("error") or result.get("raw") or result.get("message")},
                            status=sc if sc in (400, 404, 409) else 500)
            return
        self._send_json({
            "ok": True,
            "session_id": str(result.get("id") or session_id),
            "revert": result.get("revert") or None,
            "summary": result.get("summary") or None,
            "session": result,
        })

    def _handle_chat_session_abort(self) -> None:
        """POST /api/chat/session/:id/abort - interrompt l'agent en cours.

        Pass-through vers POST /session/{id}/abort d'OpenCode ; l'agent s'achève
        immédiatement (outil ou réponse coupés).
        """
        session_id = self._session_id_from_path()
        if not session_id:
            self._send_json({"ok": False, "message": "session_id manquant"}, status=400)
            return
        root = ServerHandler.project_root
        global opencode_server
        if opencode_server is None:
            self._send_json({"ok": False, "message": "OpenCode Server non disponible"}, status=503)
            return
        sc, result = opencode_server.abort_session(session_id, str(root))
        if sc not in (200, 201, 202, 204):
            self._send_json({"ok": False, "session_id": session_id,
                             "message": "Interruption refusée par OpenCode",
                             "error": result.get("error") or result.get("raw") or result.get("message")},
                            status=sc if sc in (400, 404, 409) else 500)
            return
        self._send_json({"ok": True, "session_id": session_id, "aborted": True})

    def _handle_chat_session_title(self) -> None:
        """POST /api/chat/session/:id/title - renomme la discussion.

        Simple pass-through vers PATCH /session/{id} d'OpenCode (titre natif) ;
        Sayrhazi ne touche pas SQLite.
        """
        session_id = self._session_id_from_path()
        if not session_id:
            self._send_json({"ok": False, "message": "session_id manquant"}, status=400)
            return
        root = ServerHandler.project_root
        global opencode_server
        if opencode_server is None:
            self._send_json({"ok": False, "message": "OpenCode Server non disponible"}, status=503)
            return
        body = self._read_body()
        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json({"ok": False, "message": "JSON invalide"}, status=400)
            return
        title = str(data.get("title") or "").strip()
        if not title:
            self._send_json({"ok": False, "message": "Le nom ne peut pas être vide."}, status=400)
            return
        if len(title) > 200:
            self._send_json({"ok": False, "message": "Le nom dépasse 200 caractères."}, status=400)
            return
        sc, result = opencode_server.rename_session(session_id, title, str(root))
        if sc != 200:
            self._send_json({"ok": False, "session_id": session_id,
                             "message": "Renommage refusé par OpenCode",
                             "error": (result or {}).get("error") or (result or {}).get("raw") or (result or {}).get("message")},
                            status=sc if sc in (400, 404, 409) else 500)
            return
        self._send_json({
            "ok": True,
            "session_id": session_id,
            "title": str((result or {}).get("title") or title),
        })

    def _handle_chat_current_model(self) -> None:
        """GET /api/chat/current-model?agent=<file> - modèle de l'agent.

        Le modèle vient du frontmatter de l'agent Sayrhazi (comme la TUI lit
        la config de l'agent) ; le nom lisible et l'indicateur free sont
        résolus depuis le catalogue OpenCode.
        """
        agent_file = ""
        if "?" in self.path:
            import urllib.parse
            query = self.path.split("?", 1)[1]
            for param in query.split("&"):
                if param.startswith("agent="):
                    agent_file = urllib.parse.unquote(param.split("=", 1)[1])
                    break
        agent_file = agent_file.strip()
        if not agent_file or not re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$", agent_file):
            self._send_json({"ok": False, "message": "agent invalide ou manquant"}, status=400)
            return
        root = ServerHandler.project_root
        full, _short = agent_model(root, agent_file)
        full = str(full or "").strip()
        if not full or "/" not in full:
            self._send_json({"ok": False, "message": "modèle introuvable dans le frontmatter de l'agent"}, status=400)
            return
        provider_id, model_id = full.split("/", 1)
        provider_id = provider_id.strip()
        model_id = model_id.strip()
        if not provider_id or not model_id:
            self._send_json({"ok": False, "message": "modèle incomplet dans le frontmatter de l'agent"}, status=400)
            return
        m = _model_by_id(provider_id, model_id)
        provider_name = _provider_name(provider_id)
        self._send_json({
            "ok": True,
            "model": {
                "providerID": provider_id,
                "modelID": model_id,
                "name": str(m["name"] if m else model_id),
                "providerName": provider_name,
                "free": bool(m["free"]) if m else False,
            },
        })

    def _handle_chat_find_file(self) -> None:
        """GET /api/chat/find-file?query=<q>&type=file|directory&limit=<n> - autocomplétion @.

        Recherche floue confinée au répertoire racine du projet (comme la TUI).
        Retourne des chemins relatifs à la racine, jamais le préfixe racine.
        """
        import urllib.parse
        query = ""
        file_type = "any"
        limit = 20
        dirs_only = False
        query_str = self.path.split("?", 1)[1] if "?" in self.path else ""
        for param in query_str.split("&"):
                if param.startswith("query="):
                    query = urllib.parse.unquote(param.split("=", 1)[1])
                elif param.startswith("type="):
                    file_type = urllib.parse.unquote(param.split("=", 1)[1]).strip().lower() or "any"
                elif param.startswith("limit="):
                    try:
                        limit = int(param.split("=", 1)[1])
                    except ValueError:
                        limit = 20
                elif param.startswith("dirs="):
                    dirs_only = urllib.parse.unquote(param.split("=", 1)[1]).strip().lower() == "true"
        root = ServerHandler.project_root
        if not query:
            self._send_json({"ok": True, "matches": []})
            return
        skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", ".cache",
                     "dist", "build", ".next", ".idea", ".vscode", ".mypy_cache",
                     ".gradle", "target", "bin", "obj", "coverage", ".pytest_cache",
                     ".tox", ".eggs", "site-packages"}
        q_norm = query.strip().lower()
        q_parts = [
            q_norm[i] for i in range(len(q_norm))
            if q_norm[i].isalpha() or q_norm[i].isdigit() or q_norm[i] in "._-/"
        ]
        q_needle = "".join(q_parts)
        matches = []
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
                if file_type in ("directory", "any", "") or dirs_only:
                    for d in dirnames:
                        rel = os.path.relpath(os.path.join(dirpath, d), root)
                        matches.append((rel.replace("\\", "/"), 1))
                if file_type in ("file", "any", ""):
                    for f in filenames:
                        if f.startswith("."):
                            continue
                        full = os.path.join(dirpath, f)
                        rel = os.path.relpath(full, root).replace("\\", "/")
                        matches.append((rel, 0))
            if not matches:
                self._send_json({"ok": True, "matches": []})
                return
            scored = []
            if not q_needle:
                for rel, is_dir in matches:
                    scored.append((-1, rel, is_dir))
            else:
                for rel, is_dir in matches:
                    lower = rel.lower()
                    if q_needle in lower:
                        scored.append((0, rel, is_dir))
                    elif q_needle[: max(1, int(len(q_needle) * 0.75))] in lower:
                        scored.append((1, rel, is_dir))
                    else:
                        # match flou strict : toutes les lettres du needle dans l'ordre
                        i = 0
                        ok = True
                        for ch in lower:
                            if i < len(q_needle) and ch == q_needle[i]:
                                i += 1
                        if i == len(q_needle):
                            scored.append((2, rel, is_dir))
            if not scored:
                self._send_json({"ok": True, "matches": []})
                return
            scored.sort(key=lambda item: (item[0], len(item[1]), item[1].lower()))
            seen = set()
            result = []
            for _, rel, is_dir in scored:
                if rel in seen:
                    continue
                seen.add(rel)
                result.append({"path": rel, "directory": bool(is_dir)})
                if len(result) >= limit:
                    break
            self._send_json({"ok": True, "matches": result})
        except Exception as exc:
            self._send_json({"ok": False, "message": str(exc)}, status=500)

    def _handle_chat_pending(self, session_id: str) -> None:
        """GET /api/chat/session/:id/pending - permissions et questions en attente."""
        root = ServerHandler.project_root
        global opencode_server
        if opencode_server is None:
            self._send_json({"ok": False, "message": "OpenCode Server non disponible"}, status=503)
            return
        _sc_perm, raw_permissions = opencode_server.list_permissions(str(root))
        _sc_quest, raw_questions = opencode_server.list_questions(str(root))
        permissions = []
        if isinstance(raw_permissions, list):
            for p in raw_permissions:
                if isinstance(p, dict) and p.get("sessionID") == session_id:
                    permissions.append(p)
        questions = []
        if isinstance(raw_questions, list):
            for q in raw_questions:
                if isinstance(q, dict) and q.get("sessionID") == session_id:
                    questions.append(q)
        self._send_json({"ok": True, "session_id": session_id,
                         "permission": permissions, "question": questions})

    def _chat_request_ids(self):
        parts = self.path.split("/")
        if len(parts) < 7:
            return None, None, None
        sid = parts[4] if parts[3] == "session" and len(parts) >= 5 else ""
        if not sid:
            return None, None, None
        return sid, parts[5], parts[6]

    def _handle_chat_permission_reply(self) -> None:
        """POST /api/chat/session/:id/permission/:requestID/reply - autorise/refuse un outil."""
        sid, kind, rid = self._chat_request_ids()
        if not sid or kind != "permission" or not rid:
            self._send_json({"ok": False, "message": "route invalide"}, status=400)
            return
        body = self._read_body()
        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json({"ok": False, "message": "JSON invalide"}, status=400)
            return
        reply = str(data.get("reply") or "").strip()
        if reply not in ("once", "always", "reject"):
            self._send_json({"ok": False, "message": "reply doit être once|always|reject"}, status=400)
            return
        message = str(data.get("message") or "").strip()
        root = ServerHandler.project_root
        global opencode_server
        if opencode_server is None:
            self._send_json({"ok": False, "message": "OpenCode Server non disponible"}, status=503)
            return
        sc, result = opencode_server.respond_permission(rid, reply, message)
        ok = sc in (200, 201, 204)
        self._send_json({"ok": ok, "session_id": sid, "reply": reply,
                         "error": result.get("error") or result.get("message") if not ok and isinstance(result, dict) else None},
                        status=sc if sc else 500)

    def _handle_chat_question_reply(self) -> None:
        """POST /api/chat/session/:id/question/:requestID/reply - répond aux questions de l'agent."""
        sid, kind, rid = self._chat_request_ids()
        if not sid or kind != "question" or not rid:
            self._send_json({"ok": False, "message": "route invalide"}, status=400)
            return
        body = self._read_body()
        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json({"ok": False, "message": "JSON invalide"}, status=400)
            return
        answers = data.get("answers")
        if not isinstance(answers, list):
            self._send_json({"ok": False, "message": "answers doit être un tableau"}, status=400)
            return
        root = ServerHandler.project_root
        global opencode_server
        if opencode_server is None:
            self._send_json({"ok": False, "message": "OpenCode Server non disponible"}, status=503)
            return
        sc, result = opencode_server.respond_question(rid, answers)
        ok = sc in (200, 201, 204)
        self._send_json({"ok": ok, "session_id": sid,
                         "error": result.get("error") or result.get("message") if not ok and isinstance(result, dict) else None},
                        status=sc if sc else 500)

    def _handle_chat_question_reject(self) -> None:
        """POST /api/chat/session/:id/question/:requestID/reject - rejette les questions de l'agent."""
        sid, kind, rid = self._chat_request_ids()
        if not sid or kind != "question" or not rid:
            self._send_json({"ok": False, "message": "route invalide"}, status=400)
            return
        root = ServerHandler.project_root
        global opencode_server
        if opencode_server is None:
            self._send_json({"ok": False, "message": "OpenCode Server non disponible"}, status=503)
            return
        sc, result = opencode_server.reject_question(rid)
        ok = sc in (200, 201, 204)
        self._send_json({"ok": ok, "session_id": sid,
                         "error": result.get("error") or result.get("message") if not ok and isinstance(result, dict) else None},
                        status=sc if sc else 500)

    def _handle_chat_session_messages(self, session_id: str) -> None:
        """GET /api/chat/session/:id/messages - tous les messages d'une session.

        Le contenu textuel est stocke dans la table `part` (type text/reasoning),
        pas dans `message.data` qui ne porte que les metriques.
        """
        try:
            import sqlite3
            db_path = opencode_db_path()
            if not db_path:
                self._send_json({"ok": False, "message": "base OpenCode introuvable"}, status=503)
                return
            db = sqlite3.connect("file:" + str(db_path) + "?mode=ro", uri=True, timeout=5)
            try:
                cur = db.cursor()
                # Messages de la session, ordonnes par rowid (ordre d'insertion SQLite,
                # plus fiable que time_created pour l'ordre chronologique reel d'affichage).
                cur.execute(
                    "SELECT id, data, time_created, rowid FROM message"
                    " WHERE session_id = ? ORDER BY rowid ASC",
                    (session_id,))
                msg_rows = cur.fetchall()
                messages_meta = {}
                for msg_id, data_json, mtime, mrowid in msg_rows:
                    payload = _parse_tool_data(data_json) if data_json else {}
                    role = str(payload.get("role") or "system")
                    messages_meta[msg_id] = {
                        "role": role,
                        "time_created": mtime,
                        "rowid": mrowid,
                    }

                # Tous les parts de la session, indexes par message_id
                cur.execute(
                    "SELECT id, message_id, data, time_created, rowid FROM part"
                    " WHERE session_id = ?",
                    (session_id,))
                part_rows = cur.fetchall()
            finally:
                db.close()
        except Exception as exc:
            self._send_json({"ok": False, "message": str(exc)}, status=500)
            return

        # Indexer les parts par message_id, triees par rowid (ordre d'insertion) a l'interieur de chaque message
        parts_by_msg = {}
        for part_id, part_message_id, pdata_json, pmtime, prowid in part_rows:
            parts_by_msg.setdefault(part_message_id, []).append((pdata_json, pmtime, prowid, part_id))
        for pid in parts_by_msg:
            parts_by_msg[pid].sort(key=lambda x: (x[2], x[1]))

        messages = []
        synthetic_candidates = []
        seq = 0
        for msg_id, data_json, mtime, mrowid in msg_rows:
            meta = messages_meta.get(msg_id, {})
            role = str(meta.get("role") or "system")
            role = {"user": "user", "assistant": "agent"}.get(role, role)
            time_raw = meta.get("time_created", mtime)
            seq += 1
            msg_payload = _parse_tool_data(data_json) if data_json else {}
            assistant_error = None
            if isinstance(msg_payload, dict):
                err = msg_payload.get("error")
                if isinstance(err, dict):
                    err_data = err.get("data") if isinstance(err.get("data"), dict) else {}
                    assistant_error = {
                        "name": str(err.get("name") or "UnknownError"),
                        "statusCode": err_data.get("statusCode"),
                        "message": str(err_data.get("message") or ""),
                        "isRetryable": bool(err_data.get("isRetryable", False)),
                        "responseBody": str(err_data.get("responseBody") or ""),
                    }

            ctx = {
                "pending_reasoning": None,
                "items": [], "tools_list": [],
                "raw_parts": [], "text_parts": [],
                "part_starts": [], "part_ends": [],
            }
            for pdata_json, pmtime, prowid, part_id in parts_by_msg.get(msg_id, []):
                payload = _parse_tool_data(pdata_json) if pdata_json else {}
                _part_to_item(payload, ctx, part_id)
            items = ctx["items"]
            # Les RetryParts ne sont pas rendues inline : le bandeau
            # transitoire et le bandeau rouge définitif portent l'information
            # de retry/erreur. On les dépose de la liste persistée.
            items = [it for it in items if it.get("kind") != "retry"]

            # Message `user` portant un part compaction (le tour auto/overflow
            # déclenché par OpenCode) et l'assistant `compaction` qui le suit
            # sont des artefacts internes : on ne les remontera pas à l'interface.
            is_user_compaction = role == "user" and any(
                rp.get("type") == "compaction" for rp in ctx["raw_parts"]
            )
            is_assistant_compaction = role == "agent" and isinstance(msg_payload, dict) and (
                str(msg_payload.get("agent") or "").lower() == "compaction"
                or str(msg_payload.get("mode") or "").lower() == "compaction"
            )
            if is_user_compaction or is_assistant_compaction:
                continue
            tools_list = ctx["tools_list"]
            text_parts = ctx["text_parts"]
            part_starts = ctx["part_starts"]
            part_ends = ctx["part_ends"]
            raw_parts = ctx["raw_parts"]

            # Bloc final : elements ordonnes (reasoning / outils / texte)
            text = " ".join(p for p in text_parts if p) if text_parts else ""
            if not items:
                # Message sans outil ni raisonnement (typiquement un message user) :
                # on conserve son identifiant pour identifier les users synthetiques
                # utilises par la selection native du footer (Mo/To).
                synthetic_candidates.append({
                    "id": str(msg_id),
                    "role": str(msg_payload.get("role") or ""),
                    "text": text,
                    "parts": raw_parts,
                })
                continue
            msg_duration = ""
            if part_starts and part_ends:
                try:
                    ms = int(max(part_ends)) - int(min(part_starts))
                    if ms >= 0:
                        msg_duration = _format_ms(ms)
                except (TypeError, ValueError):
                    pass
            if not msg_duration:
                msg_duration = _format_duration(msg_payload)
            # Modele : nom lisible depuis le catalogue providers OpenCode
            # (Ed() d'OpenCode), avec fallback sur modelID.
            model_key = ""
            provider_id = ""
            if isinstance(msg_payload, dict):
                provider_id = str(msg_payload.get("providerID") or "").strip()
                model_id = str(msg_payload.get("modelID") or "").strip()
                if not model_id:
                    nested = msg_payload.get("model") or {}
                    if isinstance(nested, dict):
                        provider_id = provider_id or str(nested.get("providerID") or "").strip()
                        model_id = str(nested.get("modelID") or "").strip()
                model_key = _resolve_model_name(provider_id, model_id)
            msg_time = msg_payload.get("time") or {}
            # Bornes du message au sens OpenCode (message.time).
            # Pas de repli sur les parts : les parts n'ont pas toujours de `time`
            # et cela faussait le calcul de la duree totale.
            msg_start = msg_time.get("created")
            msg_end = msg_time.get("completed")
            messages.append({
                "role": role,
                "text": text,
                "reasoning": "",
                "tools": tools_list,
                "items": items,
                "model": model_key,
                "model_provider": _provider_name(provider_id) if provider_id else "",
                "parent_id": str(msg_payload.get("parentID") or ""),
                "raw_role": str(msg_payload.get("role") or ""),
                "raw_parts": raw_parts,
                # Identifiant du message : permet de cibler le bloc agent dans
                # le DOM lors du streaming en direct (pièces remplacées en
                # place, sans recharger la conversation).
                "message_id": str(msg_id),
                "_msg_id": str(msg_id),
                "time": _format_time_ms(time_raw),
                "time_raw": time_raw,
                "metrics_only": False,
                "duration": msg_duration,
                # time.completed brut : l'interface s'en sert pour savoir si
                # le tour de l'agent est termine (indicateur « en cours »).
                "completed": msg_end,
                "_start": msg_start,
                "_end": msg_end,
                "seq": seq,
                # Erreur définitive du tour (assistant) : l'interface en déduit
                # le bandeau rouge « Upstream request failed » au rechargement.
                "error": assistant_error,
            })

        # Selection du footer agent : reproduction exacte de ko()/To()/Mo() d'OpenCode.
        #   1. o = ids des messages utilisateur SYNTHETIQUES (Mo)
        #   2. liste triee par (time.created, id) comme le store OpenCode
        #   3. parcours inverse de la liste de messages
        #   4. exclusion si To() : assistant dont le parent est un user synthetique
        #      ET qui contient un appel bash
        #   5. un seul footer par tour (parentID) : le message le plus RECENT
        #   6. footer pose si completed > created
        #   7. duree affichee = duree du TOUR (comme la TUI)
        synthetic_user_ids = set()
        for cand in synthetic_candidates:
            if _has_synthetic_user_part(cand.get("parts") or []) and _is_synthetic_user_message(
                cand.get("role") or "",
                cand.get("text") or "",
                cand.get("parts") or [],
            ):
                synthetic_user_ids.add(cand.get("id") or "")

        _select_assistant_footers(messages, synthetic_user_ids)

        # Nettoyage des champs internes
        for msg in messages:
            msg.pop("_start", None)
            msg.pop("_end", None)
            msg.pop("_msgdur", None)
            msg.pop("has_bash", None)
            msg.pop("raw_parts", None)
            msg.pop("raw_role", None)
            msg.pop("_msg_id", None)

        self._send_json({"ok": True, "session": session_id, "messages": messages})

    def _handle_chat_session_post(self) -> None:
        """POST /api/chat/session/:id/message - envoyer un message dans une session.

        L'API native d'OpenCode attend des `parts` (le champ `message` a été
        retiré en 1.19.0) ; l'agent est transmis pour que la session reste
        rattachée à l'agent choisi dans Sayrhazi.
        """
        parts = self.path.split("/")
        if len(parts) < 5:
            self._send_json({"ok": False, "message": "session manquant"}, status=400)
            return
        session_id = parts[4]
        body = self._read_body()
        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json({"ok": False, "message": "JSON invalide"}, status=400)
            return
        message = data.get("message", "")
        agent = str(data.get("agent") or "")
        attachments = data.get("attachments") if isinstance(data.get("attachments"), list) else None
        root = ServerHandler.project_root
        global opencode_server
        if opencode_server is None:
            self._send_json({"ok": False, "message": "OpenCode Server non disponible"}, status=503)
            return
        sc, result = opencode_server.send_message(session_id, message, agent, str(root))
        ok = sc in (200, 201)
        self._send_json({"ok": ok, "session_id": session_id,
                         "response": result.get("message", "") if isinstance(result, dict) else "",
                         "error": result.get("error") if isinstance(result, dict) and not ok else None})

    def _handle_chat_event(self) -> None:
        """GET /api/chat/event - événements SSE d'OpenCode Server."""
        global opencode_server
        if opencode_server is None:
            self._send_json({"ok": False, "message": "OpenCode Server non disponible"}, status=503)
            return
        sc, result = opencode_server._request("GET", "/event")
        self._send_json({"ok": sc == 200, "events": result}, status=sc if sc in (200, 201, 400, 500) else 500)

    def _handle_chat_session_prompt_async(self) -> None:
        """POST /api/chat/session/:id/prompt_async - envoyer un message de façon asynchrone (streaming)."""
        parts = self.path.split("/")
        if len(parts) < 5:
            self._send_json({"ok": False, "message": "session manquant"}, status=400)
            return
        session_id = parts[4]
        body = self._read_body()
        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json({"ok": False, "message": "JSON invalide"}, status=400)
            return
        message = data.get("message", "")
        attachments = data.get("attachments") if isinstance(data.get("attachments"), list) else None
        if not message and not attachments:
            self._send_json({"ok": False, "message": "message requis"}, status=400)
            return
        agent = str(data.get("agent") or "")
        model = data.get("model") if isinstance(data.get("model"), dict) else None
        root = ServerHandler.project_root
        global opencode_server
        if opencode_server is None:
            self._send_json({"ok": False, "message": "OpenCode Server non disponible"}, status=503)
            return
        sc, result = opencode_server.prompt_async(session_id, message, agent, str(root), model=model, attachments=attachments, root=root)
        if sc == 400 and isinstance(result, dict) and result.get("ok") is False:
            self._send_json({"ok": False,
                             "message": str(result.get("message") or "pièce jointe refusée"),
                             "code": str(result.get("code") or "ATTACH_ERROR")}, status=400)
            return
        # OpenCode répond 204 (No Content) sur prompt_async : on renvoie un
        # JSON 200 à l'interface (le message arrive par les événements SSE).
        ok = sc in (200, 201, 202, 204)
        self._send_json({"ok": ok, "session_id": session_id,
                         "response": result.get("message", "") if isinstance(result, dict) else "",
                         "error": result.get("error") if isinstance(result, dict) and not ok else None})

    def _handle_chat_session_stream(self) -> None:
        """GET /api/chat/stream?session_id=... - flux SSE du tour d'un message agent.

        Suit les événements OpenCode 1.19 pour la session demandée et émet un
        format compact :
            {kind: "message", messageID, role, completed, agent}
            {kind: "part",    messageID, partID, item}   # item = même forme que /messages
            {kind: "idle"}                                  # fin du tour
            {kind: "error",   message}                      # session.error
        Les parts sont convertis par ``_part_to_item`` (même code que le
        rechargement), donc le DOM live et le DOM rechargé restent identiques.
        """
        global opencode_server
        if opencode_server is None:
            self._send_json({"ok": False, "message": "OpenCode Server non disponible"}, status=503)
            return
        import urllib.parse

        # Extraire session_id de la query string
        session_id = ""
        if "?" in self.path:
            qs = self.path.split("?", 1)[1]
            for param in qs.split("&"):
                if param.startswith("session_id="):
                    session_id = urllib.parse.unquote(param.split("=", 1)[1])
                    break
        if not session_id:
            self._send_json({"ok": False, "message": "session_id requis"}, status=400)
            return

        root = str(ServerHandler.project_root or "")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        import http.client
        import urllib.parse
        conn = None
        try:
            conn = http.client.HTTPConnection("127.0.0.1", opencode_server.port, timeout=300)
            path = "/event"
            if root:
                path += "?directory=" + urllib.parse.quote(root.replace("\\", "/"))
            conn.request("GET", path)
            resp = conn.getresponse()
            last_beat = time.time()
            while True:
                if time.time() - last_beat > 25:
                    try:
                        self.wfile.write(b":heartbeat\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                        break
                    last_beat = time.time()
                line = resp.readline()
                if not line:
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                if not stripped.startswith(b"data:"):
                    continue
                try:
                    event = json.loads(stripped[len(b"data:"):].decode("utf-8", "replace").strip())
                except Exception:
                    continue
                compact = _translate_opencode_event(event, session_id)
                if compact is None:
                    continue
                try:
                    self.wfile.write(("data: " + json.dumps(compact, ensure_ascii=False) + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                    break
        except Exception:
            pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _handle_chat_session_event_sse(self) -> None:
        """GET /api/chat/session/event - proxy SSE du flux natif `/event`.

        Le flux `/event` d'OpenCode porte les événements de session
        (`session.created`, `session.updated` - renommage automatique - et
        `session.deleted`). Le flux `/global/event` ne porte que les
        événements globaux du serveur : la barre latérale s'abonne donc à
        `/event` pour suivre le cycle de vie des discussions.
        """
        global opencode_server
        if opencode_server is None:
            self._send_json({"ok": False, "message": "OpenCode Server non disponible"}, status=503)
            return

        root = str(ServerHandler.project_root or "")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        import http.client
        import urllib.parse
        conn = None
        try:
            conn = http.client.HTTPConnection("127.0.0.1", opencode_server.port, timeout=300)
            path = "/event"
            if root:
                path += "?directory=" + urllib.parse.quote(root.replace("\\", "/"))
            conn.request("GET", path)
            resp = conn.getresponse()
            last_beat = time.time()
            while True:
                if time.time() - last_beat > 25:
                    try:
                        self.wfile.write(b":heartbeat\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                        break
                    last_beat = time.time()
                line = resp.readline()
                if not line:
                    break
                try:
                    self.wfile.write(line)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                    break
        except Exception:
            pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _handle_chat_global_event_sse(self) -> None:
        """GET /api/chat/global/event - proxy SSE vers OpenCode /global/event."""
        global opencode_server
        if opencode_server is None:
            self._send_json({"ok": False, "message": "OpenCode Server non disponible"}, status=503)
            return

        # Extraire le session_id de la query string si présent
        session_id = ""
        if "?" in self.path:
            qs = self.path.split("?", 1)[1]
            for param in qs.split("&"):
                if param.startswith("session_id="):
                    session_id = param.split("=", 1)[1]
                    break

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        import http.client
        import urllib.parse
        conn = None
        try:
            conn = http.client.HTTPConnection("127.0.0.1", opencode_server.port, timeout=300)
            path = "/global/event"
            if session_id:
                path += "?session_id=" + urllib.parse.quote(session_id)
            conn.request("GET", path)
            resp = conn.getresponse()
            last_beat = time.time()
            while True:
                if time.time() - last_beat > 25:
                    try:
                        self.wfile.write(b":heartbeat\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                        break
                    last_beat = time.time()
                line = resp.readline()
                if not line:
                    break
                try:
                    self.wfile.write(line)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                    break
        except Exception:
            pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def main() -> int:
    global _http_server

    parser = argparse.ArgumentParser(
        description="Serveur unifié Sayrhazi (dashboard web + moteur watcher)"
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port d'écoute")
    parser.add_argument(
        "--root", type=str, default=None,
        help="Racine du projet Sayrhazi (défaut : détectée depuis le répertoire courant)",
    )
    args = parser.parse_args()

    if args.root:
        root = Path(args.root).resolve()
    else:
        root = find_project_root(Path.cwd())
    if root is None:
        print("ERREUR : racine projet introuvable (.opencode/sayrhazi.yaml absent).")
        print("       Utilisez --root <chemin> pour spécifier la racine manuellement.")
        return 2

    if not (root / ".opencode" / "sayrhazi.yaml").is_file():
        print("ERREUR : .opencode/sayrhazi.yaml absent.")
        return 2

    config, werr = parse_workflow_minimal(root / ".opencode" / "workflow.yaml")
    usable, notes = select_auto_stages(config) if config else ([], [werr or "absent"])
    if notes:
        for note in notes:
            print(f"ATTENTION : {note}")

    ServerHandler.project_root = root
    ServerHandler.engine_config = config
    ServerHandler.engine_usable = usable

    # Hook de démarrage : aligner silencieusement ~/.opencode/agent avec les
    # fiches du projet (modèles d'agents) pour qu'OpenCode les prenne en compte.
    if sync_home_opencode is not None:
        try:
            core_root = _here.parent.parent.parent
            if not (core_root / "runtimes" / "opencode" / "agents").is_dir():
                core_root = root
            sync_home_opencode(root, core_root)
        except Exception:
            pass

    global watcher_engine
    watcher_engine = WatcherEngine(root)

    # Lancer OpenCode Server pour le chat temps réel (port 4096)
    global opencode_server
    opencode_server = OpenCodeServer(root, port=4096)
    if opencode_server.start():
        print(f"OpenCode Server - http://127.0.0.1:{opencode_server.port}/")
    else:
        print("ATTENTION : OpenCode Server non disponible (opencode serve introuvable).")
        opencode_server = None

    # Aligner .opencode/models.yaml sur le catalogue OpenCode actuel : au
    # démarrage du serveur, OpenCode reste l'unique autorité sur les
    # fenêtres de contexte fournies (toujours via sayrhazi-modules).
    try:
        if opencode_server is not None:
            changed = sync_models_registry(root)
            if changed:
                print(f"Registre models.yaml : {changed} entrée(s) synchronisée(s) depuis OpenCode.")
    except Exception as exc:
        print(f"ATTENTION : sync models.yaml impossible : {exc}")

    print(f"Sayrhazi serveur - http://localhost:{args.port}/")
    print(f"Racine : {root}")
    print(f"Moteur : {len(usable)} étape(s) automatique(s)")
    print("Ctrl+C pour arrêter.")
    server = ThreadingHTTPServer(("localhost", args.port), ServerHandler)
    _http_server = server

    # serve_forever() dans le thread principal : Ctrl+C
    # delivre directement KeyboardInterrupt depuis la boucle de select() et
    # declenche l'arret propre ci-dessous (watcher, OpenCode Server, verrou).
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nArrêt du serveur.")

    try:
        server.server_close()
    except (OSError, RuntimeError):
        pass
    _http_server = None
    if watcher_engine is not None:
        watcher_engine.stop()
    if opencode_server is not None:
        opencode_server.stop()

    print("Serveur arrete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
