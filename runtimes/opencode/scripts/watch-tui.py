"""
watch-tui.py — dashboard TUI Sayrhazi (lecture seule, textual).

Vraie TUI : alternate screen, raw mode, layout, souris, resize, 60 FPS.

Le dashboard n'invente aucune métrique. Il lit l'état de coordination
(`.opencode/state/workflow-state.yaml`), les rapports (`.opencode/resume/`),
le journal (`.opencode/history/workflow-log.md`), le verrou du watcher
(`.opencode/state/watcher.lock`), et les métriques d'appel chez OpenCode :
sessions retrouvées par projet + agent + préfixe de titre, tokens du dernier
message assistant, coût. Un agent sans appel correspondant affiche `n/d`.

Usage :
    python .opencode/watch-tui.py [--interval SEC] [--lines N] [--once]
    q / Ctrl+C pour quitter.
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from textual.app import App, ComposeResult
    from textual.containers import Container, Horizontal, Vertical
    from textual.widgets import Static, DataTable, Header, Footer, Label
    from textual.timer import Timer
    from textual.reactive import reactive
except ImportError:
    print("ERREUR : 'textual' requis. Installez : pip install textual", file=sys.stderr)
    sys.exit(1)

IS_WINDOWS = os.name == "nt"

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


STATE_KEYS = ("task_id", "status", "running_agents")


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
    """État de coordination : tâche, statut, agents en cours.

    Les métriques d'appel ne sont plus ici : elles sont lues chez OpenCode
    (voir `find_sessions` / `last_assistant_message`).
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
    """Lit (task_id, stage_status) d'une section de fullstack.txt.

    Retourne (None, None) si la section ou le fichier est absent.
    """
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
    """Lit scope et controles depuis .opencode/resume/plan.txt.

    Retourne (scope, controls) ou (None, {}) si absent.
    """
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
    """Retourne les task_id déjà archivés dans fullstack-log.md."""
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
    """Registre `provider/model -> fenêtre de contexte`.

    Cherche le registre livré (à côté du script dans `.opencode/`, ou
    `core/runtime/models.yaml` depuis le dépôt), puis la surcharge projet.
    OpenCode ne stocke pas ces limites : sans registre, aucun pourcentage
    n'est affiché — jamais une valeur devinée.

    Une entrée au `context_tokens` invalide (absent, non numérique, nul ou
    négatif) est **ignorée** comme si le modèle était inconnu : la lire
    quand même produirait un pourcentage absurde.
    """
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
                    value = 0          # invalide -> traité comme inconnu
                if value > 0:
                    limits[current] = value
                current = None
    return limits


def find_sessions(db_path, directory: str, agent_file: str, prefix: str) -> list:
    """Sessions d'un agent sur le projet, de la moins active à la plus active.

    Le tri se fait sur `time_updated` (dernière activité), PAS sur
    `time_created` : une session manuelle longue peut être créée bien avant
    une session automatique tout en restant celle que l'utilisateur regarde.
    Trier par création afficherait alors la mauvaise session et un contexte
    erroné.

    Chaque session est marquée `auto` (titre portant le préfixe
    `Sayrhazi <task> <agent> `, donc lancée par le watcher) ou `manuel`
    (session ouverte à la main depuis un terminal). Les deux comptent : un
    agent appelé manuellement consomme autant de contexte qu'un appel
    automatique, et l'utilisateur veut voir les six agents.

    Aucun repli silencieux : si rien ne correspond, la liste est vide et la
    ligne affiche `n/d` — jamais la session d'un autre agent ou d'un autre
    projet.
    """
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
    """Dernière session non automatique d'un agent (appel manuel)."""
    manuelles = [s for s in sessions if not s.get("auto")]
    return manuelles[-1] if manuelles else None


def latest_auto_session(sessions: list) -> Optional[dict]:
    """Dernière session lancée par le watcher."""
    auto = [s for s in sessions if s.get("auto")]
    return auto[-1] if auto else None


def last_assistant_message(db_path, session_id: str) -> Optional[dict]:
    """Dernier message assistant **portant une mesure** de contexte.

    Reproduit la source du panneau Context d'OpenCode : le pourcentage porte
    sur CE message (pas sur le cumul de la session).

    Certains messages assistant ne mesurent rien et portent donc `tokens` a
    zero. Deux cas releves dans la base d'OpenCode :

    * le message ouvert au debut d'un run (`finish` absent) ;
    * un tour interrompu (`finish: "unknown"`) qui ecrase `finish` par une
      chaine non vide — un test sur la presence de `finish` ne l'ecarte donc
      pas.

    Un message a zero n'est pas une mesure : il ferait retomber la jauge a 0
    alors que le panneau Context d'OpenCode garde la derniere valeur reelle.
    On saute donc tout message dont le contexte vaut zero et on renvoie le
    plus recent des autres.

    La compaction n'entre pas ici : OpenCode la marque par un part
    `compaction` attache a un message **user**, jamais a un message assistant.
    Elle se lit donc comme un simple baisse de contexte, ce qui est exact.
    """
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
                    continue          # message sans mesure, pas un contexte nul
                return payload
            return None
        finally:
            db.close()
    except Exception:
        return None


def message_context_tokens(payload: dict) -> int:
    """Contexte d'un message assistant, formule du panneau Context d'OpenCode.

    input + output + reasoning + cache.read + cache.write. `cache.read` est
    inclus : c'est lui qui domine le contexte d'un appel réinjecté.
    """
    tokens = (payload or {}).get("tokens") or {}
    cache = tokens.get("cache") or {}
    return (_num(tokens.get("input")) + _num(tokens.get("output"))
            + _num(tokens.get("reasoning")) + _num(cache.get("read"))
            + _num(cache.get("write")))


def message_model_key(payload: dict) -> str:
    """Clé `provider/model` du message (jamais le seul nom du modèle)."""
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
    """Décomposition du contexte d'un message, telle qu'OpenCode l'a comptée.

   Utile parce que `cache.read` domine presque toujours : une jauge à 3 %
   n'a aucun sens tant qu'on n'a pas vu que 90 % du contexte était de la
    relecture de cache et non de la production de l'agent.
    """
    tokens = (payload or {}).get("tokens") or {}
    cache = tokens.get("cache") or {}
    return {"input": _num(tokens.get("input")),
            "cache.read": _num(cache.get("read")),
            "cache.write": _num(cache.get("write")),
            "output": _num(tokens.get("output")),
            "reasoning": _num(tokens.get("reasoning"))}


def session_activity(row) -> dict:
    """Horaires de la session affichée, lus chez OpenCode.

    OpenCode stocke `time_created` / `time_updated` en **millisecondes** :
    les passer tels quels à `format_age` donnait un delta négatif, donc `0s`
    partout. Convertis en secondes ici, une fois.

    `time_updated - time_created` n'est pas un temps de travail : c'est le
    temps pendant lequel la session est restée ouverte. Une session manuelle
    ouverte deux jours affiche donc ~2j, et c'est exact.
    """
    courante = getattr(row, "session_row", None) or {}
    created = int(courante.get("time_created") or 0)
    updated = int(courante.get("time_updated") or 0)
    if not created or not updated or updated < created:
        return {}
    return {"created": created / 1000.0, "updated": updated / 1000.0,
            "duree": (updated - created) / 1000.0}


def format_span(seconds: float) -> str:
    """Durée lisible jusqu'aux jours (`2j 03h14`), sans mensonge ni troncature."""
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
    """Barre du dernier message ; `usage=None` = limite inconnue."""
    if usage is None:
        return "·" * width
    try:
        pct = max(0.0, min(1.0, float(usage)))
    except (TypeError, ValueError):
        return "·" * width
    filled = int(round(pct * width))
    return "█" * filled + "░" * (width - filled)


class AgentRow:
    """Ligne d'un agent : rapport, appel en cours, et métriques lues chez OpenCode.

    Les métriques ne sont jamais estimées : elles viennent des enregistrements
    d'OpenCode, et un agent sans appel intracable affiche `métrique n/d`
    plutôt qu'une valeur devinée.
    """

    def __init__(self, role: str, state: dict, resume: Path, task_id: str,
                 project_root=None, model_limits: Optional[dict] = None):
        self.role = role
        self.agent_file = ROLE_AGENT_FILE[role]
        self.report = ROLE_REPORT[role]
        running = state.get("running_agents")
        running = running if isinstance(running, list) else _flow_items(running or "[]")

        # frontend/backend lisent leur section de fullstack.txt ; les autres lisent leur rapport propre.
        self.fullstack_section = None
        if self.report == "fullstack.txt":
            r_task, r_status = parse_fullstack_section(resume / self.report, role)
            self.fullstack_section = role
            r_mtime = (resume / self.report).stat().st_mtime if (resume / self.report).is_file() else 0
            # task_id vient de l'en-tête (pas de la section) : utiliser le task_id global si la section n'en a pas.
            if not r_task and task_id:
                r_task = task_id
        else:
            r_task, r_status, r_mtime = parse_report(resume / self.report)

        if self.agent_file in running:
            self.etat = "[green]EN COURS[/]"
        elif r_task and task_id and r_task == task_id:
            self.etat = f"[cyan]{r_status or 'OK'}[/]"
        elif r_task:
            self.etat = "[dim]attente[/]"
        else:
            self.etat = "[dim]—[/]"
        self.report_txt = f"{r_status or '-'} · {format_age(r_mtime or 0)}" if r_task else "-"
        self.etat_plain = ("EN COURS" if self.agent_file in running
                           else (r_status if (r_task and task_id and r_task == task_id)
                                 else ("attente" if r_task else "—")))

        self.usage = None      # % contexte du dernier message, None si inconnu
        self.usage_pct = "-"
        self.context_tokens = None
        self.calls = 0         # sessions automatiques du projet (historique)
        self.manual_calls = 0  # sessions manuelles du projet (historique)
        self.cost_session = None   # coût de la SEULE session affichée
        self.cost_projet = None    # coût cumulé de toutes les sessions du projet
        self.sid = "-"
        self.limite_connue = False
        self.limite = 0
        self.origine = None    # "auto" | "manuel"
        self.modele = ""
        self.last_payload: dict = {}
        self.session_row: dict = {}
        self.titre_session = ""

        if project_root is None:
            return
        db_path = opencode_db_path()
        if db_path is None:
            return
        prefixe = f"Sayrhazi {task_id} {self.agent_file} " if task_id else ""
        sessions = find_sessions(db_path, str(project_root), self.agent_file, prefixe)
        if not sessions:
            return
        auto = latest_auto_session(sessions)
        manuel = latest_manual_session(sessions)
        self.calls = sum(1 for s in sessions if s.get("auto"))
        self.manual_calls = sum(1 for s in sessions if not s.get("auto"))

        # La session affichee est la plus recente des deux origines, comparee
        # sur `time_updated` : c'est l'activite la plus recente de cet agent,
        # qu'il soit automatique ou manuel. Comparer sur `time_created` montrerait
        # une session automatique recente alors que l'utilisateur regarde une
        # session manuelle plus ancienne mais toujours active.
        courante = max((s for s in (auto, manuel) if s),
                       key=lambda s: s.get("time_updated") or s["time_created"])
        self.origine = "auto" if courante.get("auto") else "manuel"
        self.sid = short_session(courante["id"])
        # Donnees brutes conservees pour le panneau de detail : rien n'est
        # recalcule ni devine, le detail lit ce qu'OpenCode a enregistre.
        self.session_row = courante
        self.titre_session = str(courante.get("title") or "")

        # Deux coûts, jamais confondus : celui de la session affichée et celui
        # de tout l'historique du projet. Additionner des sessions n'a pas de
        # sens pour un contexte, mais a un sens pour une facture.
        self.cost_session = format_cost(courante.get("cost"))
        try:
            cumul = sum(float(s.get("cost") or 0.0) for s in sessions)
        except (TypeError, ValueError):
            cumul = 0.0
        self.cost_projet = format_cost(cumul)

        payload = last_assistant_message(db_path, courante["id"])
        if not payload:
            return
        self.last_payload = payload
        self.context_tokens = message_context_tokens(payload)
        self.modele = message_model_key(payload)
        limit = (model_limits or {}).get(message_model_key(payload))
        if isinstance(limit, int) and limit > 0:
            self.limite_connue = True
            self.limite = limit
            self.usage = self.context_tokens / limit
            self.usage_pct = f"{self.usage * 100:.0f} %"

    def detail_lines(self) -> list:
        """Détail de l'agent : uniquement des données lues, jamais calculées.

      La ligne du tableau reste un résumé ; tout ce qui demande une ligne
        de plus est ici. Aucun chiffre n'est estimé : `n/d` quand la donnée
        n'a pas été enregistrée par OpenCode.
        """
        out = [f"[bold]{self.agent_file}[/] — {self.role}"
               f"  ·  [bold]{self.etat_plain}[/]"]
        rapport = self.report_txt
        out.append(f"rapport   {self.report}  ·  {rapport}")
        titre = self.titre_session or "-"
        out.append(f"session   {self.sid}  ·  {self.origine or '-'}"
                   f"  ·  {titre[:46]}")
        activite = session_activity(self)
        if activite:
            out.append(f"          créée {format_age(activite['created'])}"
                       f"  ·  active {format_age(activite['updated'])}"
                       f"  ·  ouverte {format_span(activite['duree'])}"
                       f"  ·  coût {self.cost_session or 'n/d'}")
        if self.context_tokens is None:
            out.append("contexte  n/d (aucun message assistant mesuré)")
            return out
        part = message_token_split(getattr(self, "last_payload", {}) or {})
        out.append(f"modèle    {self.modele or 'n/d'}"
                   + (f"  ·  fenêtre {format_tokens(self.limite)}"
                      if self.limite_connue else "  ·  fenêtre n/d"))
        out.append(f"contexte  {format_tokens(self.context_tokens)} tokens"
                   f"  =  {self.usage_pct} de la fenêtre")
        out.append("          " + "  ".join(f"{k} {format_tokens(v)}"
                                          for k, v in part.items()))
        return out


SEP = "│"   # séparateur de blocs : jamais une valeur

# Deux blocs explicitement séparés, parce qu'ils ne mesurent pas la même chose.
#
#   bloc « session » : Session, Origine, Jauge, Tokens, %, Coût sess.
#       Décrivent UNIQUEMENT la session affichée (celle dont l'ID est donné).
#       Quand la vague suivante ouvre une session neuve, ces valeurs changent
#       avec elle. Elles ne sont jamais sommées d'une session à l'autre :
#       additionner des fenêtres de contextes différents n'a pas de sens.
#
#   bloc « projet » : Auto, Manuel, Coût proj.
#       Agrègent TOUTES les sessions de cet agent sur ce projet, toutes
#       tâches confondues. Un `Auto = 2` signifie « deux appels du watcher
#       depuis le début du projet », pas « deux appels dans la session
#       affichée ». Pour une facture, l'addition a un sens ; pour un
#       contexte, jamais.
COLUMNS = ("Agent", "Rôle",
           "Session", "Origine", "Jauge", "Tokens", "%", "Coût sess.",
           SEP,
           "Auto", "Manuel", "Coût proj.",
           SEP,
           "Rapport", "État")

GROUP_SESSION = ("Session", "Origine", "Jauge", "Tokens", "%", "Coût sess.")
GROUP_PROJET = ("Auto", "Manuel", "Coût proj.")

LEGENDE = (
    "Session  = la session affichée uniquement (change à chaque vague). "
    "Proj.  = toutes les sessions de l'agent sur le projet, tâches confondues.",
)


MARKUP_RE = re.compile(r"\[/?[a-z ]*\]")


def plain(cell: str) -> str:
    """Retire le balisage Textual (la sortie `--once` est du texte brut)."""
    return MARKUP_RE.sub("", str(cell or ""))


def row_cells(row) -> list:
    """Cellules d'une ligne. Aucune valeur inventée : `n/d` = non disponible.

    Une valeur par colonne : la jauge et le nombre de tokens sont deux
    cellules distinctes, jamais collés dans une seule (sinon l'en-tête et la
    ligne se décalent d'une case). Les séparateurs de blocs occupent leur
    place mais ne portent aucune valeur.
    """
    pct = row.usage_pct if row.usage is not None else "[dim]n/d[/]"
    tokens = (format_tokens(row.context_tokens)
              if row.context_tokens is not None else "[dim]n/d[/]")
    origine = {"auto": "watcher", "manuel": "[yellow]manuel[/]"}.get(row.origine, "-")
    session = ([row.agent_file, row.role,
                row.sid, origine, bar(row.usage, 10), tokens, pct,
                row.cost_session if row.cost_session is not None else "[dim]n/d[/]",
                f"[dim]{SEP}[/]",
                str(row.calls) if row.calls else "[dim]0[/]",
                str(row.manual_calls) if row.manual_calls else "[dim]0[/]",
                row.cost_projet if row.cost_projet is not None else "[dim]n/d[/]",
                f"[dim]{SEP}[/]",
                row.report_txt, row.etat])
    return session


class SayrhaziTUI(App):
    CSS = """
    Screen { background: $surface; }
    #header { height: 3; dock: top; background: $primary; color: $text; text-align: center; }
    #table { height: 1fr; }
    /* Les deux panneaux du bas partagent UN conteneur : deux widgets en
       dock:bottom se superposent au lieu de s'empiler, et le second peint
       par-dessus le premier — le journal masquait le detail. */
    #bottom { dock: bottom; height: 17; }
    #details { height: 7; border: solid $primary; padding: 1; }
    #logs { height: 10; border: solid $primary; padding: 1; }
    DataTable { background: $surface; }
    DataTable > .datatable--header { background: $primary; color: $text; text-style: bold; }
    DataTable > .datatable--cursor { background: $accent; color: $text; }
    .dim { color: $text-muted; }
    """

    BINDINGS = [("q", "quit", "Quitter"), ("r", "refresh", "Rafraîchir")]

    def __init__(self, project_root: Path, interval: float, log_lines: int):
        super().__init__()
        self.project_root = project_root
        self.interval = interval
        self.log_lines = log_lines
        self.model_limits = load_model_limits(project_root)
        self._timer: Timer | None = None
        # Agent dont la ligne est selectionnee. Le rafraichissement reconstruit
        # le tableau toutes les `interval` secondes : sans cette memorisation,
        # `table.clear()` renvoyait le curseur sur la premiere ligne a chaque
        # tick, ce qui rendait le clavier et la souris inutilisables.
        self.selected_role: Optional[str] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="header")
        yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
        with Vertical(id="bottom"):
            yield Static(id="details")
            yield Static(id="logs")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#table", DataTable)
        table.add_columns(*COLUMNS)
        self._timer = self.set_interval(self.interval, self.refresh_data)
        self.refresh_data()

    def refresh_data(self) -> None:
        state = read_state_minimal(self.project_root)
        task_id = str(state.get("task_id") or "-")
        status = str(state.get("status") or "-")
        resume = self.project_root / ".opencode" / "resume"
        pid, age = lock_info(self.project_root)
        scope, controls = read_plan_scope(self.project_root)
        archived = read_archived_task_ids(self.project_root)

        if pid is None:
            watcher_part = "[yellow]watcher : arrêté (pas de verrou)[/]"
        else:
            watcher_part = f"watcher PID [yellow]{pid}[/] · verrou {format_age(time.time() - age)}"

        scope_part = f"scope [cyan]{scope}[/]" if scope else "scope [dim]non déclaré[/]"
        controls_part = " · ".join(f"{k} [green]{v}[/]" if v == "OK" else f"{k} [yellow]{v}[/]"
                                   for k, v in sorted(controls.items())) if controls else "contrôles : —"
        archivé_part = f"archivé : {', '.join(sorted(archived)) or '—'}"

        header = self.query_one("#header", Static)
        header.update(
            f"Sayrhazi — [bold]{task_id}[/] [bold]{status}[/]  {watcher_part}"
            f"  ·  racine {self.project_root}\n"
            f"{scope_part}  ·  {controls_part}  ·  {archivé_part}\n"
            f"[dim]Bloc session : {', '.join(GROUP_SESSION)} — la session affichée"
            f" uniquement.[/]  [dim]Bloc projet : {', '.join(GROUP_PROJET)}"
            f" — toutes les sessions de l'agent sur le projet.[/]")

        table = self.query_one("#table", DataTable)
        selection = self.selected_role
        table.clear(columns=True)
        table.add_columns(*COLUMNS)
        for role in ROLE_ORDER:
            row = AgentRow(role, state, resume, task_id if task_id != "-" else "",
                           self.project_root, self.model_limits)
            table.add_row(*row_cells(row))
        # Restaure la ligne selectionnee : le curseur est porte par la position,
        # pas par l'identite de la ligne, et la reconstruction la reinitialise.
        if selection in ROLE_ORDER:
            table.move_cursor(row=ROLE_ORDER.index(selection))
        elif ROLE_ORDER:
            self.selected_role = ROLE_ORDER[0]

        logs = self.query_one("#logs", Static)
        log_lines = tail_lines(self.project_root / ".opencode" / "history" / "workflow-log.md", self.log_lines)
        if not log_lines:
            logs.update("[dim](aucun log)[/]")
        else:
            logs.update("\n".join(log_lines[-self.log_lines:]))

        # Detail de la ligne selectionnee. Reconstruit a chaque tick comme le
        # reste : l'agent choisi peut avoir produit un rapport entre deux ticks.
        details = self.query_one("#details", Static)
        row = None
        if self.selected_role in ROLE_ORDER:
            row = AgentRow(self.selected_role, state, resume,
                           task_id if task_id != "-" else "",
                           self.project_root, self.model_limits)
        details.update("\n".join(row.detail_lines()) if row
                       else "[dim](aucune ligne sélectionnée — flèches ou clic)[/]")

    def on_data_table_row_highlighted(self, event) -> None:
        """Mémorise l'agent sous le curseur (clic ou flèches)."""
        if 0 <= event.cursor_row < len(ROLE_ORDER):
            self.selected_role = ROLE_ORDER[event.cursor_row]

    def on_data_table_row_selected(self, event) -> None:
        """Clic ou Entrée : fige le détail sur cet agent jusqu'au tick suivant."""
        if 0 <= event.cursor_row < len(ROLE_ORDER):
            self.selected_role = ROLE_ORDER[event.cursor_row]

    def action_refresh(self) -> None:
        self.refresh_data()


def main() -> int:
    parser = argparse.ArgumentParser(description="Dashboard TUI Sayrhazi (textual)")
    parser.add_argument("--interval", type=float, default=1.0, help="refresh en secondes")
    parser.add_argument("--lines", type=int, default=10, help="lignes de log affichées")
    parser.add_argument("--once", action="store_true", help="un seul affichage puis sortie")
    args = parser.parse_args()

    root = find_project_root(Path.cwd())
    if root is None:
        print("ERREUR : racine projet introuvable (.opencode/sayrhazi.yaml absent).", flush=True)
        return 2
    interval = min(60.0, max(0.2, args.interval or 1.0))

    if args.once:
        state = read_state_minimal(root)
        task_id = str(state.get("task_id") or "-")
        status = str(state.get("status") or "-")
        resume = root / ".opencode" / "resume"
        limits = load_model_limits(root)
        pid, age = lock_info(root)
        scope, controls = read_plan_scope(root)
        archived = read_archived_task_ids(root)
        if pid is None:
            watcher = "watcher : arrêté (pas de verrou)"
        else:
            watcher = f"watcher PID {pid} · verrou {format_age(time.time() - age)}"
        scope_txt = scope or "non déclaré"
        controls_txt = ", ".join(f"{k}={v}" for k, v in sorted(controls.items())) or "—"
        archivé_txt = ", ".join(sorted(archived)) or "—"
        print(f"Sayrhazi — {task_id} [{status}]  {watcher}  ·  racine {root}")
        print(f"scope: {scope_txt}  ·  contrôles: {controls_txt}  ·  archivé: {archivé_txt}")
        print()
        print(f"Bloc session : {', '.join(GROUP_SESSION)}")
        print(f"  → la session affichée uniquement ; change à chaque vague, jamais sommée.")
        print(f"Bloc projet : {', '.join(GROUP_PROJET)}")
        print(f"  → toutes les sessions de cet agent sur le projet, tâches confondues.")
        print()
        head = (f"{'Agent':<11} {'Rôle':<10} {'Session':<12} {'Origine':<8} {'Jauge':<10} "
                f"{'Tokens':>8} {'%':>5} {'C.sess':>7} {SEP} {'Auto':>4} {'Man':>4} "
                f"{'C.proj':>7} {SEP} {'État'}")
        print(head)
        print("─" * len(head))

        def _c(cells, nom):
            return cells[COLUMNS.index(nom)]

        for role in ROLE_ORDER:
            row = AgentRow(role, state, resume, task_id if task_id != "-" else "", root, limits)
            cells = [plain(c) for c in row_cells(row)]
            identite = f"{_c(cells, 'Agent'):<11} {_c(cells, 'Rôle'):<10}"
            sess = (f"{_c(cells, 'Session'):<12} {_c(cells, 'Origine'):<8} "
                    f"{_c(cells, 'Jauge'):<10} {_c(cells, 'Tokens'):>8} "
                    f"{_c(cells, '%'):>5} {_c(cells, 'Coût sess.'):>7} {SEP} ")
            proj = (f"{_c(cells, 'Auto'):>4} {_c(cells, 'Manuel'):>4} "
                    f"{_c(cells, 'Coût proj.'):>7} {SEP} ")
            print(f"{identite}{sess}{proj}{_c(cells, 'État')}")
        print("─" * len(head))
        print("Jauge + Tokens = dernier message assistant de la session affichée")
        print("(formule du panneau Context d'OpenCode : input + output + reasoning")
        print("+ cache.read + cache.write), divisé par la fenêtre du modèle.")
        print("Ce n'est pas une mesure de capacité restante : le budget effectif peut")
        print("dépendre du fournisseur, de la compaction et des limites du runtime.")
        print(f"Session  = la session affichée. Origine = {'watcher (préfixe Sayrhazi) ou manuel.'}")
        print(f"Bloc projet : Auto / Man = nombre de sessions du projet ; C.proj = coût cumulé.")
        print("n/d      = donnée absente (aucun appel, ou modèle absent du registre).")
        print("Aucun pourcentage cumulatif : additionner des sessions n'a pas de sens.")
        print("logs récents (history/workflow-log.md) :")
        log_lines = tail_lines(root / ".opencode" / "history" / "workflow-log.md", max(1, args.lines))
        if not log_lines:
            print("  (aucun log)")
        else:
            for line in log_lines:
                print("  " + line)
        return 0

    app = SayrhaziTUI(root, interval, max(1, args.lines))
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())