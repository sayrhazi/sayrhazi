#!/usr/bin/env python3
"""Gestionnaire de services Sayrhazi (Lot 7) — séparé du moteur d'agents.

Les serveurs frontend/backend sont des processus persistants appartenant au
projet, pas aux agents : démarrés une fois, ils survivent aux sessions
d'agents. Ce script les démarre en arrière-plan, vérifie leurs ports,
capture leurs logs et signale un arrêt. Redémarrage jamais automatique :
un arrêt peut être volontaire (toujours explicite via `restart`).

Sans dépendance externe. Windows / Linux.

Usage :
    python .opencode/services.py start --name frontend --cmd "yarn dev" --cwd frontend --port 5173 --url http://localhost:5173
    python .opencode/services.py stop --name frontend
    python .opencode/services.py restart --name frontend --cmd "yarn dev" --cwd frontend --port 5173
    python .opencode/services.py status [--name frontend]
    python .opencode/services.py logs --name frontend [--lines 50]
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.request import urlopen
from urllib.error import HTTPError, URLError

IS_WINDOWS = os.name == "nt"


def _opencode_dir(project_root: Path) -> Path:
    return Path(project_root) / ".opencode"


def _pidfile(project_root: Path, name: str) -> Path:
    return _opencode_dir(project_root) / "state" / f"services-{name}.pid"


def _default_log(project_root: Path, name: str) -> Path:
    return _opencode_dir(project_root) / "history" / f"services-{name}.log"


def _now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def pid_alive(pid: int) -> bool:
    """Processus vivant ? (sans os.kill, bloquant sur Windows si mort)."""
    if IS_WINDOWS:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x00100000, False, pid)
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def port_busy(port: int) -> bool:
    """Port TCP local occupé ?"""
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=2):
            return True
    except OSError:
        return False


def http_alive(url: str, timeout: int = 5) -> bool:
    """URL joignable ? Toute réponse HTTP (même erreur) = serveur vivant."""
    if not url or not url.lower().startswith(("http://", "https://")):
        return False
    try:
        with urlopen(url, timeout=timeout) as response:
            return response.status is not None
    except HTTPError:
        return True
    except (URLError, ValueError, TimeoutError, OSError):
        return False


def read_pidfile(project_root: Path, name: str) -> Optional[dict]:
    """Contenu du pidfile (dict) ou None."""
    path = _pidfile(project_root, name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("pid"), int):
        return None
    return data


def _wait_gone(pid: int, timeout: int = 5) -> bool:
    """Attend la fin du processus. Retourne True s'il est parti."""
    end = time.time() + max(0, timeout)
    while time.time() < end:
        if not pid_alive(pid):
            return True
        time.sleep(0.5)
    return not pid_alive(pid)


def _terminate_tree(pid: int) -> None:
    """Tue le processus et ses enfants (arbre complet)."""
    if IS_WINDOWS:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (OSError, ProcessLookupError, PermissionError):
        try:
            os.kill(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError, PermissionError):
            pass


def start_service(project_root: Path, name: str, cmd: str, cwd: str = ".",
                  port: Optional[int] = None, url: Optional[str] = None,
                  wait: int = 30, log: Optional[str] = None) -> tuple:
    """Démarre un service détaché. Retourne (ok, message)."""
    root = Path(project_root)
    current = read_pidfile(root, name)
    if current and pid_alive(current["pid"]):
        return False, f"{name} déjà démarré (PID {current['pid']})"
    if port is not None and port_busy(port):
        return False, f"port {port} occupé : {name} non démarré (vérifie quel processus l'occupe)"
    workdir = root / cwd if not os.path.isabs(cwd) else Path(cwd)
    if not workdir.is_dir():
        return False, f"dossier introuvable : {workdir}"
    log_path = Path(log) if log else _default_log(root, name)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(str(log_path), "ab", buffering=0)
    except OSError as exc:
        return False, f"log inaccessible {log_path} : {exc}"
    try:
        if IS_WINDOWS:
            # CREATE_NEW_PROCESS_GROUP seul : isole du Ctrl+C sans couper les
            # logs (DETACHED_PROCESS fait perdre la sortie via cmd, vérifié).
            flags = 0x00000200
            proc = subprocess.Popen(cmd, shell=True, stdout=log_file, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, cwd=str(workdir),
                                    creationflags=flags)
        else:
            proc = subprocess.Popen(shlex.split(cmd), shell=False, stdout=log_file,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    cwd=str(workdir), start_new_session=True)
    except (OSError, ValueError) as exc:
        try:
            log_file.close()
        except OSError:
            pass
        return False, f"lancement impossible ({cmd}) : {exc}"
    try:
        log_file.close()  # l'enfant garde sa copie ; le parent ne retient rien
    except OSError:
        pass
    try:
        info = {"pid": proc.pid, "port": port, "cmd": cmd, "cwd": str(workdir),
                "started_at": _now_iso()}
        _pidfile(root, name).parent.mkdir(parents=True, exist_ok=True)
        _pidfile(root, name).write_text(json.dumps(info), encoding="utf-8")
    except OSError:
        pass
    if url:
        end = time.time() + max(0, wait)
        while time.time() < end:
            if http_alive(url):
                return True, f"{name} démarré (PID {proc.pid}), URL répond : {url}"
            if not pid_alive(proc.pid):
                return False, f"{name} s'est arrêté pendant le démarrage (voir {log_path})"
            time.sleep(1)
        alive_note = "URL répond" if http_alive(url) else f"URL sans réponse après {wait}s (voir {log_path})"
        return pid_alive(proc.pid), f"{name} démarré (PID {proc.pid}), {alive_note}"
    time.sleep(2)
    if pid_alive(proc.pid):
        return True, f"{name} démarré (PID {proc.pid})"
    return False, f"{name} s'est arrêté aussitôt (voir {log_path})"


def stop_service(project_root: Path, name: str) -> tuple:
    """Arrête un service (arbre complet). Retourne (ok, message)."""
    root = Path(project_root)
    current = read_pidfile(root, name)
    if current is None:
        return False, f"{name} non démarré (pas de pidfile)"
    pid = current["pid"]
    if not pid_alive(pid):
        try:
            _pidfile(root, name).unlink()
        except OSError:
            pass
        return True, f"{name} déjà arrêté (pidfile périmé nettoyé)"
    _terminate_tree(pid)
    if _wait_gone(pid):
        try:
            _pidfile(root, name).unlink()
        except OSError:
            pass
        return True, f"{name} arrêté (PID {pid})"
    return False, f"{name} refuse de s'arrêter (PID {pid}, tue-le à la main)"


def service_status(project_root: Path, name: Optional[str] = None) -> list:
    """État des services : [{name, pid, alive, port, port_busy, url, url_ok}]."""
    root = Path(project_root)
    state_dir = _opencode_dir(root) / "state"
    names = [name] if name else sorted(
        p.name[len("services-"):-len(".pid")] for p in state_dir.glob("services-*.pid")
        if p.name.startswith("services-") and p.name.endswith(".pid"))
    rows = []
    for service in names:
        info = read_pidfile(root, service) or {}
        pid = info.get("pid")
        alive = pid_alive(pid) if isinstance(pid, int) else False
        port = info.get("port")
        url = None
        rows.append({"name": service, "pid": pid, "alive": alive, "port": port,
                     "port_busy": port_busy(port) if port else False,
                     "url": url, "url_ok": None,
                     "started_at": info.get("started_at")})
    return rows


def tail_logs(project_root: Path, name: str, lines: int = 50) -> tuple:
    """Dernières lignes du log (texte, erreur)."""
    candidate = _default_log(project_root, name)
    if not candidate.is_file():
        return "", f"aucun log pour {name}"
    try:
        content = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return "", f"log illisible : {exc}"
    return "\n".join(content[-max(1, lines):]), ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Gestionnaire de services Sayrhazi (hors agents)")
    parser.add_argument("--project", default=".", help="racine du projet")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="démarrer un service détaché")
    start.add_argument("--name", required=True)
    start.add_argument("--cmd", required=True, help="commande, ex. \"yarn dev\"")
    start.add_argument("--cwd", default=".")
    start.add_argument("--port", type=int, default=None)
    start.add_argument("--url", default=None)
    start.add_argument("--wait", type=int, default=30)
    start.add_argument("--log", default=None)

    stop = sub.add_parser("stop", help="arrêter un service")
    stop.add_argument("--name", required=True)

    restart = sub.add_parser("restart", help="redémarrer explicitement un service")
    restart.add_argument("--name", required=True)
    restart.add_argument("--cmd", required=True)
    restart.add_argument("--cwd", default=".")
    restart.add_argument("--port", type=int, default=None)
    restart.add_argument("--url", default=None)
    restart.add_argument("--wait", type=int, default=30)
    restart.add_argument("--log", default=None)

    status = sub.add_parser("status", help="état des services")
    status.add_argument("--name", default=None)

    logs = sub.add_parser("logs", help="fin du journal")
    logs.add_argument("--name", required=True)
    logs.add_argument("--lines", type=int, default=50)

    args = parser.parse_args()
    root = Path(args.project).resolve()
    if not (_opencode_dir(root) / "sayrhazi.yaml").is_file():
        print("ERREUR : projet Sayrhazi introuvable (sayrhazi.yaml absent).")
        return 2

    if args.command == "start":
        ok, message = start_service(root, args.name, args.cmd, args.cwd, args.port,
                                    args.url, args.wait, args.log)
        print(("OK " if ok else "KO ") + message)
        return 0 if ok else 1
    if args.command in ("stop", "restart"):
        if args.command == "restart":
            stop_service(root, args.name)
            ok, message = start_service(root, args.name, args.cmd, args.cwd, args.port,
                                        args.url, args.wait, args.log)
            print(("OK " if ok else "KO ") + message)
            return 0 if ok else 1
        ok, message = stop_service(root, args.name)
        print(("OK " if ok else "KO ") + message)
        return 0 if ok else 1
    if args.command == "status":
        rows = service_status(root, args.name)
        if not rows:
            print("Aucun service connu (lance `start`).")
            return 0
        for row in rows:
            state = "en cours" if row["alive"] else "arrêté"
            extra = f"port {row['port']} ({'occupé' if row['port_busy'] else 'libre'})" if row["port"] else "sans port"
            print(f"{row['name']} : {state} (PID {row['pid']}), {extra}")
        return 0
    if args.command == "logs":
        text, err = tail_logs(root, args.name, args.lines)
        if err:
            print("KO " + err)
            return 1
        print(text)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
