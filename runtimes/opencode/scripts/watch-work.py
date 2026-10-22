"""
watch-work.py — moteur de transitions Sayrhazi (Lots 4-6).

Lit `.opencode/workflow.yaml` et déclenche, pour chaque étape `auto: true`,
l'agent du rôle lorsque : le rapport déclencheur est stable, son `task_id`
et son statut sont autorisés, la condition de l'étape est satisfaite, et le
rapport de sortie ne couvre pas déjà ce `task_id`. Les étapes éligibles
ensemble partent en parallèle (Lot 6, bornées par `max_parallel_agents`),
avec convergence journalisée ; sans config valide, repli sur la transition
historique : `build.txt TERMINÉ -> Hadji`.

Garde-fous : verrou d'instance unique, anti-double en mémoire + sortie
existante (survit au redémarrage, point 7 du contrat), minuteur visible,
journal `history/workflow-log.md`, mise à jour minimale de
`state/workflow-state.yaml` (la réconciliation complète reste
`engine/state.py --reconcile`). Ne boucle pas après verdict négatif, ne
publie / supprime rien.

Sans dépendance externe. Windows / Linux.

Usage :
    python .opencode/watch-work.py [--once] [--check] [--timeout SEC] [--continue]
    --once    : une seule vérification puis sortie (0 = déclenché, 1 = rien)
    --check   : valide config sans surveiller (idéal pour tests)
    --timeout : durée max de surveillance en secondes (0 = infini)
    --continue: diagnostic de l'arrêt précédent (causes, purge) puis reprise
"""

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

IS_WINDOWS = os.name == "nt"
AGENT_TO_TRIGGER = "hadji"
POLL_INTERVAL_SECONDS = 3
DEBOUNCE_SECONDS = 2
LOCK_STALE_SECONDS = 600

# Rôle -> fichier agent OpenCode (noms stables) et rapport produit.
ROLE_AGENT_FILE = {"architect": "lawibrahim", "build-front": "ali",
                   "build-back": "bamse", "reviewer": "hadji",
                   "security": "hifadhui", "qa": "zawadi",
                   "frontend": "ali", "backend": "bamse"}
ROLE_REPORT = {"architect": "plan.txt", "build-front": "frontend.txt",
               "build-back": "backend.txt", "reviewer": "review.txt",
               "security": "security.txt", "qa": "qa.txt",
               "frontend": "fullstack.txt", "backend": "fullstack.txt"}
ROLE_STATUS_OK = {"build-back": ("TERMINÉ",), "reviewer": ("VALIDÉ", "VALIDÉ AVEC RÉSERVES"),
                  "security": ("VALIDÉ", "VALIDÉ AVEC RÉSERVES"),
                  "qa": ("VALIDÉ", "VALIDÉ AVEC RÉSERVES"),
                  "build-front": ("VALIDÉ",), "architect": ("VALIDÉ",),
                  "frontend": ("TERMINÉ",), "backend": ("TERMINÉ",)}

# Condition déclarée -> cle lue dans sayrhazi.yaml (section quality).
# design_required n'a pas d'indicateur : Ali reste manuel (Lot 4).
CONDITION_KEYS = {"security_required": "security_audit_required_by_default",
                  "visual_qa_required": "visual_qa_required"}

AGENT_PROMPTS = {
    "build-back": "Build Sayrhazi task {task} : lis .opencode/resume/plan.txt et produis backend.txt",
    "reviewer": "Review Sayrhazi task {task} : lis .opencode/resume/backend.txt et produis review.txt",
    "security": "Audit Sayrhazi task {task} : lis .opencode/resume/backend.txt et produis security.txt",
    "qa": "QA Sayrhazi task {task} : lis .opencode/resume/backend.txt et produis qa.txt",
    "build-front": "Design Sayrhazi task {task} : lis .opencode/resume/plan.txt et produis frontend.txt",
    "frontend": "Frontend Sayrhazi task {task} : lis .opencode/resume/plan.txt, ecris uniquement ta section de .opencode/resume/fullstack.txt",
    "backend": "Backend Sayrhazi task {task} : lis .opencode/resume/fullstack.txt (section frontend doit etre TERMINÉ), ecris uniquement ta section de .opencode/resume/fullstack.txt",
}

# Cloisonnement d'écriture entre agents concurrents : chaque agent ne
# produit QUE son rapport (lire les autres est utile, les écrire est
# interdit). L'archivage vers .opencode/history/ reste autorisé.
OWN_REPORT = {"build-back": "backend.txt", "reviewer": "review.txt", "security": "security.txt",
              "qa": "qa.txt", "build-front": "frontend.txt",
              "frontend": "fullstack.txt", "backend": "fullstack.txt"}
ALL_REPORTS = ("plan.txt", "design.txt", "build.txt", "review.txt", "security.txt", "qa.txt", "frontend.txt", "backend.txt", "fullstack.txt")


def isolation_clause(agent: str) -> str:
    """Clause anti-débordement : seul le rapport de l'agent peut être écrit."""
    own = OWN_REPORT.get(agent or "", "")
    others = ", ".join(r for r in ALL_REPORTS if r != own)
    return (f"Ne produis QUE .opencode/resume/{own} : n'écris jamais {others} "
            f"(lecture autorisée, écriture interdite ; archivage vers .opencode/history/ autorisé).")

TASK_RE = re.compile(r"^\s*task_id\s*:\s*(\S+)\s*$", re.MULTILINE | re.IGNORECASE)
STATUS_RE = re.compile(r"^\s*status\s*:\s*(\S.*?)\s*$", re.MULTILINE | re.IGNORECASE)
SECTION_HEADER_RE = re.compile(r"^## Étape (frontend|backend) — (Ali|Bamse)$")


def log(message: str) -> None:
    stamp = paint(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]", "green")
    print(f"{stamp} {style_line(message)}", flush=True)


# --- Sessions OpenCode : une session neuve par appel, titres traçables ---
#
# Le watcher ne fait plus le travail d'OpenCode : il ne lit pas sa base, ne
# calcule aucun usage, ne fixe aucun seuil et ne déclenche ni rotation ni
# compaction. Chaque appel ouvre une session neuve (`opencode run` sans
# `--session`) et la titre de façon déterministe et unique :
#
#     Sayrhazi <task_id> <agent> <run_id>
#
# Le titre est la seule chose que le watcher produit pour l'observation ; le
# dashboard (watch-tui.py) retrouve ensuite chaque session par ce préfixe et
# y lit les métriques qu'OpenCode a lui-même enregistrées (tokens du dernier
# message assistant, coût de session). La mémoire d'un agent reste dans les
# rapports (`.opencode/resume/`), pas dans un historique réinjecté.
#
# Historique : jusqu'à 0.12.x le watcher réutilisait `--session` par
# (tâche, agent) et estimait la saturation avec des compteurs cumulés en
# excluant `tokens_cache_read`. Or cette valeur est précisément la majeure
# partie du contexte renvoyé à chaque appel : la jauge tombait à ~0 % pendant
# que le panneau Context d'OpenCode affichait la valeur réelle. Supprimé.

RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")

# Unicité du run_id : le suffixe ne doit pas dépendre du PID seul (identique
# pour tous les threads d'une vague parallèle). On alterne un compteur de
# séquence et un sel aléatoire par processus : deux appels du même agent
# dans la même seconde, et deux watcher distincts, donnent deux titres
# distincts. Le verrou d'instance reste la vraie protection contre deux
# watchers sur un même projet.
_RUN_SEQ_LOCK = threading.Lock()
_RUN_SEQ = 0
# Sel d'instance : PID et millisecondes de démarrage, réduits à un octet.
_RUN_SALT_N = (os.getpid() ^ int(time.time() * 1000)) & 0xFF


def build_run_id(start: Optional[float] = None) -> str:
    """Identifiant d'appel unique et triable : horodatage + suffixe 4 hex.

    Le suffixe combine un compteur de séquence (unique même sous concurrence
    dans le processus) et un sel par watcher (unique entre deux instances),
    réduits en exactement 4 signes hexadécimaux.
    """
    global _RUN_SEQ
    with _RUN_SEQ_LOCK:
        _RUN_SEQ = (_RUN_SEQ + 1) % 0x10000
        seq = _RUN_SEQ
    # seq sur 8 bits hauts, sel (1 octet) sur les 8 bits bas.
    suffix = f"{(seq >> 8) & 0xFF:02x}{(seq & 0xFF) ^ _RUN_SALT_N:02x}"
    stamp = datetime.fromtimestamp(start if start is not None else time.time())
    return f"{stamp.strftime('%Y%m%d-%H%M%S')}-{suffix}"


def session_title(task_id: str, agent_file: str, run_id: str) -> str:
    """Titre de session : préfixe stable + identifiant d'appel."""
    return f"Sayrhazi {task_id} {agent_file} {run_id}"


ANSI_COLORS = {"reset": "\x1b[0m", "cyan": "\x1b[36m", "yellow": "\x1b[33m",
               "green": "\x1b[32m", "red": "\x1b[31m", "magenta": "\x1b[35m"}

TASK_HL_RE = re.compile(r"\b[A-Z]+-\d[\d-]*")
PCT_RE = re.compile(r"\b\d{1,3}\s?%")


def _color_enabled() -> bool:
    """Couleurs console uniquement (jamais journal, jamais sortie redirigée)."""
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def paint(text: str, color: str) -> str:
    """Colore un fragment pour la console (texte brut si couleurs coupées)."""
    if not _color_enabled() or color not in ANSI_COLORS:
        return text
    return ANSI_COLORS[color] + text + ANSI_COLORS["reset"]


def strip_ansi(text: str) -> str:
    """Retire les séquences ANSI (garde-fou journal)."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text or "")


ANSI_SPLIT_RE = re.compile(r"(\x1b\[[0-9;]*m)")


def _style_plain(text: str) -> str:
    """Peinture d'un fragment sans séquence ANSI (agents, task_id, %, alertes)."""
    styled = re.sub(r"\bERREUR\b", paint("ERREUR", "red"), text)
    styled = re.sub(r"\bATTENTION\b", paint("ATTENTION", "yellow"), styled)
    for name in sorted(set(ROLE_AGENT_FILE.values()), key=len, reverse=True):
        styled = re.sub(r"\b" + re.escape(name) + r"\b", paint(name, "cyan"), styled)
    styled = TASK_HL_RE.sub(lambda m: paint(m.group(0), "yellow"), styled)
    styled = PCT_RE.sub(lambda m: paint(m.group(0), "green"), styled)
    return styled


def style_line(message: str) -> str:
    """Mots importants d'une ligne console ; segments déjà colorés intacts."""
    if not _color_enabled():
        return message
    parts = ANSI_SPLIT_RE.split(message or "")
    for i in range(0, len(parts), 2):
        parts[i] = _style_plain(parts[i])
    return "".join(parts)


def parse_model_id(raw) -> str:
    """Id de modèle depuis la colonne `model` (JSON) ou une chaîne simple."""
    text = str(raw or "").strip()
    if text.startswith("{"):
        try:
            return str(json.loads(text).get("id", "") or "")
        except (ValueError, AttributeError):
            return ""
    return text


def short_model(model_id: str) -> str:
    """Nom court d'affichage (suffixe provider retiré, `-free` allégé)."""
    short = (model_id or "").split("/")[-1]
    if short.endswith("-free"):
        short = short[: -len("-free")]
    return short


def agent_model(project_root: Path, agent_file: str) -> Tuple[str, str]:
    """(modèle complet, modèle court) depuis le frontmatter de l'agent."""
    try:
        text = (project_root / ".opencode" / "agent" / (agent_file + ".md")).read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return "", ""
    match = re.search(r"^model\s*:\s*(\S+)", text, re.MULTILINE)
    full = match.group(1).strip() if match else ""
    return full, short_model(full)


TICK_SECONDS = 30


def format_duration(seconds: float) -> str:
    """Duree compacte : 5s, 8s, 1m, 2m14s."""
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    minutes, rest = divmod(total, 60)
    return f"{minutes}m" if rest == 0 else f"{minutes}m{rest:02d}s"


def find_project_root(start: Path) -> Optional[Path]:
    """Remonte depuis start jusqu'à trouver .opencode/sayrhazi.yaml."""
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".opencode" / "sayrhazi.yaml").is_file():
            return candidate
        if (candidate / ".opencode" / "agent").is_dir() and (candidate / "AGENTS.md").is_file():
            return candidate
    # Cas script installé dans .opencode/ : racine = parent de .opencode/
    if start.name == ".opencode" and (start / "sayrhazi.yaml").is_file():
        return start.parent
    try:
        script_parent = Path(__file__).resolve().parent
        if script_parent.name == ".opencode":
            return script_parent.parent
        if (script_parent.parent / ".opencode" / "sayrhazi.yaml").is_file():
            return script_parent.parent
    except NameError:
        pass
    return None


def file_hash(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None
    except OSError as exc:
        log(f"ERREUR lecture {path}: {exc}")
        return None


def parse_build_report(path: Path) -> Tuple[Optional[str], Optional[str]]:
    """Extrait (task_id, status) depuis build.txt. Retourne (None, None) si absent."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").lstrip(chr(0xFEFF))
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        log(f"ERREUR lecture {path}: {exc}")
        return None, None
    task = TASK_RE.search(text)
    status = STATUS_RE.search(text)
    task_id = task.group(1).strip() if task else None
    stat = status.group(1).strip().upper() if status else None
    return task_id, stat


def _strip_hash(line: str) -> str:
    out: list[str] = []
    quote: Optional[str] = None
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "#" and out and out[-1] == " ":
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _scalar(value: str):
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    low = text.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    return text


def parse_workflow_minimal(path: Path) -> Tuple[Optional[dict], Optional[str]]:
    """Sous-ensemble YAML pour workflow.yaml (miroir d'engine/workflow.py).

    Mappings imbriqués (2 espaces) + listes `- ` + commentaires. Retourne
    (config, None) ou (None, erreur). Toute forme hors sous-ensemble = erreur
    claire, jamais devinée.
    """
    if not path.is_file():
        return None, "absent"
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").lstrip(chr(0xFEFF)).splitlines()
    except OSError as exc:
        return None, f"illisible : {exc}"
    items: list[tuple[int, int, str]] = []
    for lineno, raw in enumerate(raw_lines, 1):
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        line = _strip_hash(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent % 2:
            return None, f"ligne {lineno} : indentation impaire"
        items.append((lineno, indent, line.strip()))

    def block(pos: int, indent: int) -> Tuple[dict, int]:
        data: dict = {}
        while pos < len(items):
            lineno, ind, content = items[pos]
            if ind < indent:
                break
            if ind > indent:
                raise _WorkflowSyntax(f"ligne {lineno} : inattendu `{content}`")
            if content.startswith("- ") or ":" not in content:
                raise _WorkflowSyntax(f"ligne {lineno} : attendu `cle: valeur`")
            key, _, value = content.partition(":")
            key, value = key.strip(), value.strip()
            if value == "":
                if pos + 1 < len(items) and items[pos + 1][1] > indent:
                    child, pos = _node(pos + 1, items[pos + 1][1])
                    data[key] = child
                else:
                    data[key] = None
                    pos += 1
            else:
                data[key] = _scalar(value)
                pos += 1
        return data, pos

    def _node(pos: int, indent: int):
        if items[pos][2].startswith("- "):
            return seq(pos, indent)
        return block(pos, indent)

    def seq(pos: int, indent: int) -> Tuple[list, int]:
        out: list = []
        while pos < len(items):
            lineno, ind, content = items[pos]
            if ind < indent or not content.startswith("- "):
                break
            if ind > indent:
                raise _WorkflowSyntax(f"ligne {lineno} : inattendu `{content}`")
            rest = content[2:].strip()
            if ":" in rest and not rest.startswith(("[", "'", '"')):
                key, _, value = rest.partition(":")
                key, value = key.strip(), value.strip()
                item = {key: _scalar(value)} if value else {key: None}
                pos += 1
                if value == "" and pos < len(items) and items[pos][1] > indent:
                    child, pos = _node(pos, items[pos][1])
                    item[key] = child
                elif pos < len(items) and items[pos][1] > indent and not items[pos][2].startswith("- "):
                    sub, pos = block(pos, items[pos][1])
                    item.update(sub)
                out.append(item)
            else:
                out.append(_scalar(rest))
                pos += 1
        return out, pos

    try:
        data, pos = block(0, 0)
    except _WorkflowSyntax as exc:
        return None, str(exc)
    if pos != len(items):
        return None, f"ligne {items[pos][0]} : inattendu `{items[pos][2]}`"
    return data, None


class _WorkflowSyntax(ValueError):
    pass


def read_conditions(project_root: Path) -> dict:
    """Lit les indicateurs quality.* de sayrhazi.yaml (défaut False)."""
    conditions = {name: False for name in ("security_required", "visual_qa_required", "design_required")}
    config = project_root / ".opencode" / "sayrhazi.yaml"
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return conditions
    mapping = {"security_required": "security_audit_required_by_default",
               "visual_qa_required": "visual_qa_required"}
    for condition, key in mapping.items():
        match = re.search(r"^\s*" + re.escape(key) + r"\s*:\s*(\S+)", text, re.MULTILINE | re.IGNORECASE)
        if match and match.group(1).strip().lower() == "true":
            conditions[condition] = True
    return conditions


def output_covers(output_file: Path, task_id: str, trigger_mtime: float) -> bool:
    """Point 7 généralisé : le rapport de sortie couvre-t-il ce task_id ?

    Vrai si le fichier existe, mentionne le task_id et est plus récent que le
    déclencheur (un nouveau rapport déclencheur relance légitimement).
    """
    if not output_file.is_file():
        return False
    try:
        text = output_file.read_text(encoding="utf-8", errors="replace")
        mtime = output_file.stat().st_mtime
    except OSError:
        return False
    return task_id in text and mtime >= trigger_mtime


def fullstack_section_status(path: Path, section: str) -> str | None:
    """Lit le stage_status d'une section de fullstack.txt.

    Retourne le statut (ex. "TERMINÉ") ou None si la section est absente.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")
    except OSError:
        return None
    current = None
    for raw in text.splitlines():
        m = SECTION_HEADER_RE.match(raw.strip())
        if m:
            current = m.group(1)
            continue
        if current == section:
            mm = re.match(r"^\s*stage_status\s*:\s*(\S.*)$", raw, re.IGNORECASE)
            if mm:
                return mm.group(1).strip().strip('"').strip("'")
    return None


def eligible_transitions(config: dict, triggers: dict, conditions: dict, outputs: dict,
                         fullstack_sections: dict | None = None) -> list:
    """Étapes éligibles, sans effet de bord (testable).

    config : workflow parsé. triggers : {rapport: {task_id, status, mtime}}
    (rapports stables uniquement). conditions : {nom: bool}. outputs :
    {rapport: {task_id, mtime}} (rapports de sortie existants).
    fullstack_sections : {nom_section: statut} optionnel, pour verrouiller
    l'étape backend tant que la section frontend n'est pas TERMINÉ.
    Retourne [(stage, task_id, raison), ...] pour les stages auto: true.
    """
    eligible: list = []
    stages = config.get("stages") if isinstance(config, dict) else None
    if not isinstance(stages, list):
        return eligible
    for stage in stages:
        if not isinstance(stage, dict) or not stage.get("auto", False):
            continue
        sid = stage.get("id", "?")
        trigger = stage.get("trigger") or {}
        report, status = trigger.get("report"), trigger.get("status")
        current = triggers.get(report)
        if current is None:
            continue
        task_id = current.get("task_id")
        if not task_id or current.get("status") != (status or "").strip().upper():
            continue
        condition = stage.get("condition")
        if condition and not conditions.get(condition, False):
            continue
        agent = stage.get("agent")
        output_report = ROLE_REPORT.get(agent or "")
        if not output_report:
            continue
        # Règle fullstack : l'étape backend ne démarre que si la section
        # frontend de fullstack.txt est TERMINÉ.
        if agent == "backend" and output_report == "fullstack.txt":
            if fullstack_sections is None or fullstack_sections.get("frontend") != "TERMINÉ":
                continue
        out = outputs.get(output_report)
        if out and out.get("task_id") == task_id and out.get("mtime", 0) >= current.get("mtime", 0):
            continue
        eligible.append((stage, task_id, f"{sid} prêt ({task_id})"))
    return eligible


def parse_report_file(path: Path) -> Tuple[Optional[str], Optional[str], Optional[float]]:
    """(task_id, status, mtime) d'un rapport, ou (None, None, None)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").lstrip(chr(0xFEFF))
        mtime = path.stat().st_mtime
    except OSError:
        return None, None, None
    task = TASK_RE.search(text)
    status = STATUS_RE.search(text)
    return ((task.group(1).strip() if task else None),
            (status.group(1).strip().upper() if status else None), mtime)


def select_auto_stages(config: Optional[dict]) -> Tuple[list, list]:
    """Étapes automatiques utilisables + notes (étapes cassées ignorées)."""
    notes: list = []
    usable: list = []
    stages = config.get("stages") if isinstance(config, dict) else None
    if not isinstance(stages, list) or not stages:
        return [], ["stages: liste non vide requise"]
    known_reports = set(ROLE_REPORT.values())
    for i, stage in enumerate(stages):
        where = f"stages[{i}]"
        if not isinstance(stage, dict) or not stage.get("id"):
            notes.append(f"{where} ignorée (id absent)")
            continue
        if stage.get("agent") not in ROLE_AGENT_FILE:
            notes.append(f"{where} ignorée (agent `{stage.get('agent')}` inconnu)")
            continue
        if not stage.get("auto", False):
            continue
        trigger = stage.get("trigger") or {}
        if trigger.get("report") not in known_reports:
            notes.append(f"{where} ignorée (trigger.report `{trigger.get('report')}` inconnu)")
            continue
        if not trigger.get("status"):
            notes.append(f"{where} ignorée (trigger.status absent)")
            continue
        usable.append(stage)
    return usable, notes


def lock_path(project_root: Path) -> Path:
    return project_root / ".opencode" / "state" / "watcher.lock"


def lock_holder_pid(project_root: Path) -> Optional[int]:
    """PID inscrit dans le verrou, ou None si illisible."""
    try:
        content = lock_path(project_root).read_text(encoding="utf-8", errors="replace").strip()
        return int(content.split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _pid_alive(pid: int) -> bool:
    """Processus vivant ? Sans os.kill (bloquant sur Windows si mort)."""
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


def holder_alive(project_root: Path) -> bool:
    """Le détenteur du verrou respire-t-il encore ? (anti-purge abusive)."""
    pid = lock_holder_pid(project_root)
    if pid is None:
        return False
    return _pid_alive(pid)


def touch_lock(project_root: Path) -> None:
    """Heartbeat : prouve qu'on est vivant (verrou jamais périmé à tort)."""
    try:
        if lock_holder_pid(project_root) == os.getpid():
            os.utime(str(lock_path(project_root)), None)
    except OSError:
        pass


def acquire_lock(project_root: Path) -> bool:
    """Verrou d'instance unique.

    Un verrou périmé (10 min) n'est purgé que si son détenteur est mort :
    un run Hadji de plus de 10 min reste protégé (heartbeat + PID vivant).
    """
    path = lock_path(project_root)
    try:
        os.makedirs(str(path.parent), exist_ok=True)
    except OSError:
        return True
    for _ in range(2):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, f"{os.getpid()} {datetime.now().isoformat()}".encode("utf-8"))
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except OSError:
                return False
            if age >= LOCK_STALE_SECONDS and not holder_alive(project_root):
                try:
                    path.unlink()
                except OSError:
                    return False
                continue
            return False
        except OSError:
            return True
    return False


def release_lock(project_root: Path) -> None:
    """Ne libère que SON verrou (jamais celui d'une instance plus récente)."""
    if lock_holder_pid(project_root) == os.getpid():
        try:
            lock_path(project_root).unlink()
        except OSError:
            pass


def clean_stop_path(project_root: Path) -> Path:
    return project_root / ".opencode" / "state" / "clean_stop"


def write_clean_stop(project_root: Path) -> None:
    """Marqueur d'arrêt propre (Ctrl+C géré). Absent = arrêt brutal."""
    try:
        path = clean_stop_path(project_root)
        os.makedirs(str(path.parent), exist_ok=True)
        path.write_text(datetime.now().astimezone().replace(microsecond=0).isoformat(),
                        encoding="utf-8")
    except OSError:
        pass


def read_clean_stop(project_root: Path) -> Optional[str]:
    try:
        text = clean_stop_path(project_root).read_text(encoding="utf-8", errors="replace").strip()
        return text or None
    except OSError:
        return None


def clear_clean_stop(project_root: Path) -> None:
    try:
        clean_stop_path(project_root).unlink()
    except OSError:
        pass


def diagnose_restart(project_root: Path) -> None:
    """Diagnostic `--continue` : causes de l'arrêt précédent, purge, reprise.

    Preuves durables uniquement (journal append-only, état, marqueur) :
    un Ctrl+C au repos laisse un marqueur propre et aucun agent en cours ;
    un arrêt brutal laisse des agents en cours et/ou des appels sans
    terminaison au journal. Purge les marqueurs périmés, la boucle
    normale reprend le travail inachevé.
    """
    log("diagnostic --continue : analyse de l'arrêt précédent...")
    state = read_state_minimal(project_root)
    task_id = str(state.get("task_id") or "")
    running = state.get("running_agents")
    running = running if isinstance(running, list) else _flow_items(running or "[]")
    marker = read_clean_stop(project_root)
    clear_clean_stop(project_root)
    if marker:
        log(f"arrêt propre (Ctrl+C) le {marker}.")
    else:
        log("pas de marqueur d'arrêt propre (terminal fermé ou processus tué ?).")
    if running and task_id:
        log(f"interruption : {', '.join(running)} retrouvé(s) en cours sur {task_id}"
            f" — purge, reprise au prochain cycle.")
        journal(project_root, f"interruption détectée (--continue) : {', '.join(running)}"
                              f" en cours sur {task_id}, purge")
        with _SHARED_LOCK:
            write_state_minimal(project_root, {"running_agents": []})
    elif task_id:
        log(f"aucun agent en cours sur {task_id}.")
    else:
        log("aucune tâche suivie.")
    try:
        lines = (project_root / ".opencode" / "history" / "workflow-log.md").read_text(
            encoding="utf-8", errors="replace").splitlines()[-40:]
    except OSError:
        lines = []
    pending: dict = {}
    for line in lines:
        match = re.search(r"appel de (\w+)", line)
        if match:
            pending[match.group(1)] = line
            continue
        done = re.search(r"(\w+) (terminé|terminée|échoué|échouée)\b", line)
        if done and done.group(1) in pending:
            pending.pop(done.group(1), None)
            continue
        if "timeout" in line.lower() or "QUOTA" in line or "rotation" in line:
            for name in list(pending):
                if name in line:
                    pending.pop(name, None)
    for name in sorted(pending):
        log(f"interruption en cours de {name} (appel sans terminaison au journal).")
    if any("QUOTA" in line for line in lines):
        log("pause quota enregistrée (oubliée au redémarrage : la reprise retentera).")
    journal(project_root, "diagnostic --continue effectué.")


def journal(project_root: Path, message: str) -> None:
    """Archive un événement dans history/workflow-log.md."""
    try:
        history = project_root / ".opencode" / "history" / "workflow-log.md"
        os.makedirs(str(history.parent), exist_ok=True)
        with open(history, "a", encoding="utf-8") as fh:
            fh.write(f"- [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError as exc:
        log(f"ATTENTION : journal inaccessible ({exc})")


STATE_KEYS = ("task_id", "status", "started_at", "current_stages", "completed_stages",
              "pending_stages", "blocked_stages", "running_agents")


def _qscalar(value) -> str:
    text = "" if value is None else str(value)
    if text == "" or any(c in text for c in ":#{}[],&*?|-<>!=@`\"'"):
        return '"' + text.replace('"', '\\"') + '"'
    return text


def _flow_items(text: str) -> list:
    stripped = (text or "").strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        inner = stripped[1:-1].strip()
        if not inner:
            return []
        return [_unquote(part) for part in inner.split(",")]
    return [stripped] if stripped else []


def _unquote(text: str) -> str:
    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in ("'", '"'):
        return stripped[1:-1]
    return stripped


def read_state_minimal(project_root: Path) -> dict:
    """Lit les clefs connues de workflow-state.yaml (sans dépendance).

    L'état ne décrit plus de session : les métriques d'appel vivent dans la
    base d'OpenCode et le bloc `session:` d'un ancien état est volontairement
    ignoré (lu comme une clef inconnue), jamais réécrit.
    """
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


def write_state_minimal(project_root: Path, values: dict) -> bool:
    """Fusionne values dans workflow-state.yaml (clefs connues uniquement).

    Mise à jour minimale d'exécution (running_agents, tâche, statut) ; la
    réconciliation complète des étapes reste engine/state.py --reconcile.
    """
    path = project_root / ".opencode" / "state" / "workflow-state.yaml"
    try:
        os.makedirs(str(path.parent), exist_ok=True)
    except OSError:
        return False
    merged = read_state_minimal(project_root)
    for key, value in values.items():
        if key in STATE_KEYS:
            merged[key] = value
    if not merged.get("started_at"):
        merged["started_at"] = datetime.now().astimezone().replace(microsecond=0).isoformat()
    lines = ["workflow:"]
    for key in STATE_KEYS:
        value = merged.get(key)
        if key.endswith("_stages") or key == "running_agents":
            items = value if isinstance(value, list) else _flow_items(value or "[]")
            lines.append("  " + key + ": [" + ", ".join(_qscalar(x) for x in items) + "]")
        else:
            lines.append("  " + key + ": " + _qscalar(value))
    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def note_state_running(project_root: Path, task_id: str, agent: Optional[str]) -> None:
    """Signale un agent en cours (ou la fin si agent None). Chemin historique."""
    running = [agent] if agent else []
    current = read_state_minimal(project_root)
    values: dict = {"task_id": task_id, "status": "IN_PROGRESS", "running_agents": running}
    if agent is None:
        if current.get("task_id") != task_id:
            return
        values = {"running_agents": running}
    write_state_minimal(project_root, values)


_SHARED_LOCK = threading.Lock()


def state_agents_add(project_root: Path, task_id: str, agent: Optional[str]) -> None:
    """Ajoute un agent aux running_agents (parallélisme, thread-safe)."""
    if not agent:
        return
    with _SHARED_LOCK:
        current = read_state_minimal(project_root)
        items = current.get("running_agents")
        running = items if isinstance(items, list) else _flow_items(items or "[]")
        if agent not in running:
            running.append(agent)
        write_state_minimal(project_root, {"task_id": task_id, "status": "IN_PROGRESS",
                                           "running_agents": running})


def state_agents_remove(project_root: Path, task_id: str, agent: Optional[str]) -> None:
    """Retire un agent des running_agents (parallélisme, thread-safe)."""
    if not agent:
        return
    with _SHARED_LOCK:
        current = read_state_minimal(project_root)
        if current.get("task_id") != task_id:
            return
        items = current.get("running_agents")
        running = items if isinstance(items, list) else _flow_items(items or "[]")
        if agent in running:
            running.remove(agent)
        write_state_minimal(project_root, {"running_agents": running})


def max_parallel_agents(config: Optional[dict]) -> int:
    """Garde-fou machine (défaut 3)."""
    try:
        value = (config or {}).get("workflow", {}).get("max_parallel_agents", 3)
    except AttributeError:
        return 3
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return 3
    return value


def classify_failure(result) -> str:
    """Classe une réponse d'erreur d'opencode : quota | timeout | network | other."""
    text = (str(getattr(result, "stdout", "") or "") + " "
            + str(getattr(result, "stderr", "") or "")).lower()
    lower = text
    for marker in ("rate limit", "quota", "429", "resource_exhausted", "insufficient_quota"):
        if marker in lower:
            return "quota"
    for marker in ("timed out", "timeout", "econn", "enotfound", "unreachable", "network"):
        if marker in lower:
            return "network"
    return "other"


def _run_branch(project_root: Path, stage: dict, task_id: str) -> Tuple[bool, str]:
    """Exécute une transition (journal + état + déclenchement). (ok, kind)."""
    sid = stage.get("id", "?")
    journal(project_root, f"{sid} déclenchée pour {task_id}")
    state_agents_add(project_root, task_id, stage.get("agent"))
    try:
        ok, kind = trigger_stage(project_root, stage, task_id)
    finally:
        state_agents_remove(project_root, task_id, stage.get("agent"))
    journal(project_root, f"{sid} {'terminée' if ok else 'échouée'} pour {task_id}")
    return ok, kind


def run_branches(project_root: Path, items: list, workers: int) -> tuple:
    """Exécute les transitions éligibles : séquentiel si 1, parallèle sinon.

    items : [(cle, stage, task_id, raison), ...]. Retourne (ok_quelconque,
    [clés réussies], [clés échouées]). Un échec n'empêche jamais les autres.
    """
    done: list = []
    failed: list = []
    if not items:
        return False, done, failed

    sids = sorted({(stage.get("id", "?") or "?") for _k, stage, _t, _w in items})
    wave = f"vague : {', '.join(sids)} ({items[0][2]})"
    print()
    log("########## " + wave + " ##########")
    journal(project_root, wave)

    if len(items) <= 1:
        for key, stage, task_id, why in items:
            log(f"État : {why}")
            ok, kind = _run_branch(project_root, stage, task_id)
            if ok:
                done.append(key)
            else:
                failed.append((key, stage.get("id", "?"), kind))
                if kind == "quota":
                    log(f"⚠ QUOTA atteint par {stage.get('agent')} — attente de réinitialisation.")
                    journal(project_root, f"QUOTA : pause ({stage.get('agent')})")
                    return False, done, failed
                log("Échec déclenchement, on réessaiera au prochain passage.")
        return bool(done), done, failed

    quota_hit = False
    quota_agent = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers),
                                               thread_name_prefix="sayrhazi") as pool:
        futures = {pool.submit(_run_branch, project_root, stage, task_id): (key, stage.get("id", "?"))
                   for key, stage, task_id, _why in items}
        quota_hit = False
        quota_agent = None
        for future in concurrent.futures.as_completed(futures):
            key, sid = futures[future]
            try:
                ok, kind = future.result()
            except Exception as exc:
                log(f"État : {sid} erreur inattendue ({exc})")
                journal(project_root, f"{sid} erreur inattendue ({exc})")
                ok, kind = False, "other"
            if ok:
                done.append(key)
            else:
                failed.append((key, sid, kind))
                if kind == "quota":
                    quota_hit = True
                    quota_agent = stage.get("agent")
                    log(f"⚠ QUOTA atteint par {stage.get('agent')} — mise en pause de la vague.")
        if quota_hit:
            log(f"⚠ QUOTA global détecté ({quota_agent}) — vague en pause. Attente de réinitialisation du quota.")
            journal(project_root, f"QUOTA global : pause vague ({quota_agent})")
            return False, done, failed
    names = {key: stage.get("id", "?") for key, stage, _t, _w in items}
    kinds = {key: kind for key, _sid, kind in failed}
    parts = ([f"{names[k]} OK" for k in sorted(done, key=names.get)]
             + [f"{names[k]} ÉCHEC" + (f" ({kinds[k]})" if kinds.get(k) not in (None, 'other') else "")
                for k in sorted([f for f, _s, _k in failed], key=names.get)])
    log(f"Convergence : {', '.join(parts)} ({len(done)}/{len(items)})")
    journal(project_root, f"convergence ({len(done)}/{len(items)}) : {', '.join(parts)}")
    return bool(done), done, failed


def check_opencode_available() -> bool:
    try:
        cmd = "opencode --version" if IS_WINDOWS else ["opencode", "--version"]
        result = subprocess.run(cmd, capture_output=True, text=True, shell=IS_WINDOWS, encoding="utf-8", errors="replace")
        if result.returncode == 0:
            log(f"opencode détecté : {result.stdout.strip()}")
            return True
        log("ATTENTION : 'opencode --version' a échoué.")
        return False
    except FileNotFoundError:
        log("ATTENTION : commande 'opencode' introuvable dans le PATH.")
        return False


def build_command(agent_file: str, prompt: str, title: str, model: str) -> list:
    """Commande `opencode run` : agent + titre unique + modèle forcé.

    Aucun `--session` : chaque appel ouvre une session neuve et laisse
    OpenCode gérer son contexte (compaction, cache) sans interférence. Le
    modèle du frontmatter est la source de vérité : changer de modèle =
    éditer la fiche de l'agent, jamais un paramètre d'exécution.
    """
    cmd = ["opencode", "run", "--agent", agent_file, "--title", title]
    if model:
        cmd += ["--model", model]
    return cmd + [prompt]


def output_verified(project_root: Path, output_report: str, task_id: str) -> bool:
    """Le rapport de sortie couvre-t-il le task_id ? (fichier + mention)."""
    if not output_report:
        return True
    output = project_root / ".opencode" / "resume" / output_report
    if not output.is_file():
        return False
    try:
        text = output.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return task_id in text


def trigger_stage(project_root: Path, stage: dict, task_id: str) -> Tuple[bool, str]:
    """Déclenche l'agent d'une étape et vérifie son rapport de sortie. (ok, kind)."""
    agent = (stage.get("agent") or "").strip().lower()
    agent_file = ROLE_AGENT_FILE.get(agent, agent or AGENT_TO_TRIGGER)
    output_report = ROLE_REPORT.get(agent, "")
    trigger = stage.get("trigger") or {}
    prompt = AGENT_PROMPTS.get(agent, "Sayrhazi task {task} : avance l'étape " + str(stage.get("id", "?")))
    prompt = prompt.format(task=task_id) + " " + isolation_clause(agent)
    full_model, model_short = agent_model(project_root, agent_file)
    start = time.time()
    run_id = build_run_id(start)
    title = session_title(task_id, agent_file, run_id)
    call = f"{trigger.get('report')} {trigger.get('status')} ({task_id}) -> appel de {agent_file}"
    if model_short:
        call += f" [{model_short}]"
    journal(project_root, call + (f" (modèle {full_model})" if model_short else "")
            + f" [session {title}]")
    log(call + "...")
    stop_tick = threading.Event()

    def tick() -> None:
        while not stop_tick.wait(TICK_SECONDS):
            touch_lock(project_root)
            log(f"{agent_file} en cours... {format_duration(time.time() - start)} ecoulees ({task_id})")

    ticker = threading.Thread(target=tick, daemon=True)
    ticker.start()

    def _invoke():
        parts = build_command(agent_file, prompt, title, full_model)
        if IS_WINDOWS:
            command_str = " ".join(f'"{p}"' if " " in p else p for p in parts)
            return subprocess.run(command_str, cwd=str(project_root), capture_output=True, text=True, shell=True, timeout=600, encoding="utf-8", errors="replace")
        return subprocess.run(parts, cwd=str(project_root), capture_output=True, text=True, timeout=600, encoding="utf-8", errors="replace")

    try:
        result = _invoke()
    except FileNotFoundError:
        log("ERREUR : commande 'opencode' introuvable.")
        stop_tick.set()
        ticker.join()
        return False, "opencode_missing"
    except subprocess.TimeoutExpired:
        log(f"ERREUR : timeout 600s sur appel {agent_file} ({task_id}, {format_duration(time.time() - start)} ecoulees).")
        stop_tick.set()
        ticker.join()
        if output_verified(project_root, output_report, task_id):
            log(f"{output_report} produit malgré le timeout (processus bloqué après écriture).")
            journal(project_root, f"{output_report} produit malgré le timeout pour {task_id}.")
            return True, "ok"
        return False, "timeout"
    except Exception as exc:
        log(f"ERREUR inattendue : {exc}")
        stop_tick.set()
        ticker.join()
        return False, "other"
    stop_tick.set()
    ticker.join()
    if result.stdout:
        print()
        log(f"============ AGENT · {agent_file} ============")
        sys.stdout.write(result.stdout + ("" if result.stdout.endswith("\n") else "\n"))
        sys.stdout.flush()
        log(f"============ FIN · {agent_file} ============")
        print()
    if result.returncode != 0:
        log(f"opencode erreur {result.returncode} :")
        if result.stderr:
            print(result.stderr)
        return False, classify_failure(result)
    log(f"{agent_file} terminé pour {task_id} en {format_duration(time.time() - start)}.")
    if not output_report:
        return True, "ok"
    output = project_root / ".opencode" / "resume" / output_report
    if not output.is_file():
        log(f"ATTENTION : {output_report} absent après déclenchement. Vérifie la session {agent_file}.")
        return False, "other"
    try:
        text = output.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log(f"ATTENTION : {output_report} illisible ({exc}).")
        return False, "other"
    if task_id not in text:
        log(f"ATTENTION : {output_report} ne mentionne pas {task_id}.")
        return False, "other"
    log(f"{output_report} vérifié pour {task_id}.")
    return True, "ok"


def trigger_agent(project_root: Path, task_id: str) -> bool:
    """Chemin historique build.txt TERMINÉ -> Hadji (repli sans config)."""
    ok, _kind = trigger_stage(project_root, {"id": "review", "agent": "reviewer",
                                             "trigger": {"report": "build.txt", "status": "TERMINÉ"}},
                              task_id)
    return ok


def should_trigger(build_file: Path, last_trigger: str) -> Tuple[bool, str, Optional[str]]:
    """Retourne (déclencher?, clé_unique, raison)."""
    current_hash = file_hash(build_file)
    if current_hash is None:
        return False, last_trigger, "build.txt absent"
    # Stabilité : relire après debounce
    time.sleep(DEBOUNCE_SECONDS)
    stable_hash = file_hash(build_file)
    if stable_hash != current_hash:
        return False, last_trigger, "fichier instable (écriture en cours)"
    task_id, status = parse_build_report(build_file)
    if not task_id:
        return False, last_trigger, "task_id absent — rapport incomplet, refusé"
    if status != "TERMINÉ":
        return False, last_trigger, f"status={status or 'absent'} — attendu TERMINÉ, refusé"
    # Point 7 du contrat : un review.txt plus récent que build.txt et mentionnant
    # le même task_id prouve que Hadji a déjà traité cette version (même après
    # redémarrage du watcher, où l'anti-double en mémoire est perdu). Un nouveau
    # build de Bamse (mtime plus récent) relance légitimement la transition.
    review_file = build_file.parent / "review.txt"
    if review_file.is_file():
        try:
            review_text = review_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            review_text = ""
        if task_id in review_text:
            try:
                if review_file.stat().st_mtime >= build_file.stat().st_mtime:
                    return False, last_trigger, f"review.txt existe déjà pour {task_id} — déjà traité"
            except OSError:
                pass
    key = f"{task_id}:{stable_hash}"
    if key == last_trigger:
        return False, last_trigger, f"{task_id} déjà déclenché"
    return True, key, f"{task_id} prêt"


def main() -> int:
    parser = argparse.ArgumentParser(description="Moteur de transitions Sayrhazi (workflow.yaml, repli build.txt -> Hadji)")
    parser.add_argument("--once", action="store_true", help="une seule vérification")
    parser.add_argument("--check", action="store_true", help="valide config sans surveiller")
    parser.add_argument("--timeout", type=int, default=0, help="durée max en secondes (0=infini)")
    parser.add_argument("--continue", dest="cont", action="store_true",
                        help="diagnostic de l'arrêt précédent puis reprise")
    args = parser.parse_args()

    root = find_project_root(Path.cwd())
    if root is None:
        log("ERREUR : racine projet introuvable (.opencode/sayrhazi.yaml absent). Lance depuis le projet.")
        return 2
    log(f"Racine du projet : {root}")
    build_file = root / ".opencode" / "resume" / "build.txt"

    if not (root / ".opencode" / "sayrhazi.yaml").is_file():
        log("ERREUR : .opencode/sayrhazi.yaml absent.")
        return 2
    if args.check:
        ok = check_opencode_available()
        task_id, status = parse_build_report(build_file) if build_file.is_file() else (None, None)
        log(f"check: build.txt task_id={task_id} status={status}")
        config, werr = parse_workflow_minimal(root / ".opencode" / "workflow.yaml")
        if werr or config is None:
            log(f"check: workflow.yaml inutilisable ({werr or 'absent'}) : repli build.txt -> Hadji")
        else:
            usable, notes = select_auto_stages(config)
            for note in notes:
                log(f"check: étape ignorée : {note}")
            log(f"check: workflow.yaml {len(usable)} étape(s) automatique(s) : "
                + ", ".join(s.get("id", "?") for s in usable))
        log("check OK" if ok else "check OK (opencode absent, surveillance possible)")
        return 0

    check_opencode_available()
    if not os.access(root, os.W_OK):
        log("ERREUR : racine projet non inscriptible.")
        return 2

    if not acquire_lock(root):
        holder = lock_holder_pid(root)
        if holder is not None:
            log(f"Une instance tourne déjà (PID {holder}, verrou .opencode/state/watcher.lock). Arrêt.")
        else:
            log("Une instance tourne déjà (verrou .opencode/state/watcher.lock). Arrêt.")
        return 1
    try:
        config, werr = parse_workflow_minimal(root / ".opencode" / "workflow.yaml")
        usable, notes = select_auto_stages(config) if config else ([], [werr or "absent"])
        if not usable:
            if notes:
                log(f"ATTENTION : workflow.yaml inutilisable ({notes[0]}) : repli build.txt -> Hadji.")
            return run_legacy(root, args, build_file)
        for note in notes:
            log(f"ATTENTION : {note}")
        log(f"Workflow : {len(usable)} étape(s) automatique(s) : "
            + ", ".join(s.get("id", "?") for s in usable))
        return run_generic(root, args, config, usable)
    finally:
        release_lock(root)


def watched_reports(usable: list) -> list:
    """Rapports déclencheurs des étapes automatiques, triés et uniques."""
    names = []
    for stage in usable:
        report = (stage.get("trigger") or {}).get("report")
        if report and report not in names:
            names.append(report)
    return sorted(names)


def run_generic(root: Path, args, config: Optional[dict], usable: list) -> int:
    """Boucle moteur : toute transition déclarée et éligible est déclenchée."""
    if getattr(args, "cont", False):
        diagnose_restart(root)
    watched = watched_reports(usable)
    log("Surveillance de : " + ", ".join(str(root / ".opencode" / "resume" / name) for name in watched))
    resume = root / ".opencode" / "resume"
    last_keys: set = set()
    last_hashes: dict = {}
    attempts: dict = {}
    retry_due: dict = {}
    quota_blocked: set = set()
    exhausted: set = set()
    start = time.time()

    MAX_ATTEMPTS = 3
    BACKOFF = (30, 90, 270)
    last_desc = [""]

    def snapshot() -> Tuple[dict, dict]:
        triggers: dict = {}
        outputs: dict = {}
        for name in watched:
            path = resume / name
            task_id, status, mtime = parse_report_file(path)
            if task_id is None:
                continue
            triggers[name] = {"task_id": task_id, "status": status, "mtime": mtime or 0.0,
                              "hash": file_hash(path)}
        for name in ROLE_REPORT.values():
            path = resume / name
            if not path.is_file():
                continue
            task_id, _status, mtime = parse_report_file(path)
            if task_id is not None:
                outputs[name] = {"task_id": task_id, "mtime": mtime or 0.0}
        return triggers, outputs

    def describe(triggers: dict) -> str:
        return "; ".join(f"{name} {info.get('task_id')}/{info.get('status')}"
                         for name, info in sorted(triggers.items())) or "aucun rapport"

    def evaluate() -> bool:
        triggers, outputs = snapshot()
        conditions = read_conditions(root)
        found = eligible_transitions({"stages": usable}, triggers, conditions, outputs)
        desc = describe(triggers)
        if desc != last_desc[0]:
            log(f"État : {desc}")
            last_desc[0] = desc
        todo = []
        for stage, task_id, why in found:
            trigger_report = (stage.get("trigger") or {}).get("report")
            key = f"{stage.get('id')}:{task_id}:{triggers.get(trigger_report, {}).get('hash')}"
            if key in last_keys:
                log(f"État : {task_id} déjà déclenché pour {stage.get('id')}")
                continue
            if key in quota_blocked:
                log(f"État : {stage.get('agent')} bloqué quota (voir ci-dessus), pas de relance auto")
                continue
            if key in exhausted:
                continue
            todo.append((key, stage, task_id, why))
        if not todo:
            return False
        workers = min(max_parallel_agents(config), len(todo))
        ok_any, done_keys, failed_keys = run_branches(root, todo, workers)
        for key in done_keys:
            last_keys.add(key)
            attempts.pop(key, None)
            retry_due.pop(key, None)
            exhausted.discard(key)
        for key, sid, kind in failed_keys:
            attempts[key] = attempts.get(key, 0) + 1
            if kind in ("timeout", "network") and attempts[key] < MAX_ATTEMPTS:
                delay = BACKOFF[min(attempts[key] - 1, len(BACKOFF) - 1)]
                retry_due[key] = time.time() + delay
                log(f"{sid} : erreur réseau/timeout. Nouvelle tentative dans {delay}s ({attempts[key]}/{MAX_ATTEMPTS})...")
            elif kind in ("timeout", "network"):
                exhausted.add(key)
                log(f"{sid} : 3 tentatives épuisées, échec normal pour cette version.")
            elif kind == "quota":
                quota_blocked.add(key)
                log(f"{sid} : échec — quota/rate limit du provider atteint.")
                log("Aucun retry automatique pour éviter une boucle sur le quota.")
                log("Réessayez plus tard ou relancez le watcher.")
            else:
                log(f"{sid} : échec normal ({kind}), journalisé.")
        if ok_any:
            log("En attente d'une autre tâche... (Ctrl+C pour arrêter)")
        return ok_any

    def one_cycle() -> bool:
        changed = False
        for name in watched:
            current = file_hash(resume / name)
            if current != last_hashes.get(name):
                last_hashes[name] = current
                if current is None:
                    changed = True
                    continue
                time.sleep(DEBOUNCE_SECONDS)
                stable = file_hash(resume / name)
                if stable != current:
                    log(f"État : {name} instable (écriture en cours)")
                    last_hashes[name] = stable
                    continue
                last_hashes[name] = stable
                changed = True
        if not changed:
            now = time.time()
            due_keys = [key for key, ts in retry_due.items() if ts <= now]
            if not due_keys:
                return False
            for key in due_keys:
                retry_due.pop(key, None)
        return evaluate()

    if args.once:
        return 0 if one_cycle() else 1

    log("En attente... (Ctrl+C pour arrêter)")
    last_touch = start
    try:
        while True:
            if args.timeout and (time.time() - start) > args.timeout:
                log("Timeout atteint, arrêt.")
                return 1
            one_cycle()
            if time.time() - last_touch >= 60:
                touch_lock(root)
                last_touch = time.time()
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        write_clean_stop(root)
        log("Arrêt (Ctrl+C).")
        return 0


def run_legacy(root: Path, args, build_file: Path) -> int:
    """Chemin historique build.txt TERMINÉ -> Hadji (sans config)."""
    if getattr(args, "cont", False):
        diagnose_restart(root)
    log(f"Surveillance de : {build_file}")
    last_trigger = ""
    last_touch = time.time()
    start = time.time()

    def one_pass() -> bool:
        nonlocal last_trigger
        trigger, key, reason = should_trigger(build_file, last_trigger)
        log(f"État : {reason}")
        if trigger and trigger_agent(root, parse_build_report(build_file)[0] or "UNKNOWN"):
            last_trigger = key
            return True
        if trigger:
            log("Échec déclenchement, on réessaiera au prochain passage.")
        return False

    if args.once:
        return 0 if one_pass() else 1

    log("En attente... (Ctrl+C pour arrêter)")
    last_hash_seen: Optional[str] = None
    try:
        while True:
            if args.timeout and (time.time() - start) > args.timeout:
                log("Timeout atteint, arrêt.")
                return 1
            current = file_hash(build_file)
            if current != last_hash_seen:
                last_hash_seen = current
                if one_pass():
                    log("En attente d'une autre tâche... (Ctrl+C pour arrêter)")
            if time.time() - last_touch >= 60:
                touch_lock(root)
                last_touch = time.time()
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        write_clean_stop(root)
        log("Arrêt (Ctrl+C).")
        return 0


if __name__ == "__main__":
    sys.exit(main())
