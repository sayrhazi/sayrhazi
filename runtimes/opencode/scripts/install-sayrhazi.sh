#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  echo "Usage: $0 /chemin/vers/projet"
  exit 2
}

[[ $# -eq 1 ]] || usage
PROJECT_PATH="$(realpath "$1")"
SOURCE_ROOT="$(realpath "$(dirname "$0")/../../..")"
ADAPTER_ROOT="$SOURCE_ROOT/runtimes/opencode"

[[ -d "$PROJECT_PATH" ]] || { echo "Erreur: projet introuvable: $PROJECT_PATH" >&2; exit 1; }

OPENCODE_DIR="$PROJECT_PATH/.opencode"
AGENT_DIR="$OPENCODE_DIR/agent"
mkdir -p "$AGENT_DIR" "$OPENCODE_DIR/resume" "$OPENCODE_DIR/history" "$OPENCODE_DIR/features" "$OPENCODE_DIR/state" "$OPENCODE_DIR/backup"

# Les agents sont fournis par Sayrhazi et peuvent être mis à jour.
cp "$ADAPTER_ROOT"/agents/*.md "$AGENT_DIR/"

# Les fichiers contrôlés par le projet ne sont jamais écrasés.
if [[ ! -e "$PROJECT_PATH/AGENTS.md" ]]; then
  cp "$ADAPTER_ROOT/templates/AGENTS.md" "$PROJECT_PATH/AGENTS.md"
fi

if [[ ! -e "$OPENCODE_DIR/sayrhazi.yaml" ]]; then
  cp "$ADAPTER_ROOT/templates/sayrhazi.yaml" "$OPENCODE_DIR/sayrhazi.yaml"
fi

if [[ ! -e "$OPENCODE_DIR/opencode.json" ]]; then
  cp "$ADAPTER_ROOT/templates/opencode.json" "$OPENCODE_DIR/opencode.json"
fi

if [[ ! -e "$OPENCODE_DIR/workflow.yaml" ]]; then
  cp "$ADAPTER_ROOT/templates/workflow.yaml" "$OPENCODE_DIR/workflow.yaml"
fi

# Moteur + briques partagées + serveur unifié (cohérent avec engine/updater.py).
for script in watch-work.py services.py sayrhazi-modules.py sayrhazi-serveur.py; do
  if [[ -e "$ADAPTER_ROOT/scripts/$script" ]]; then
    cp "$ADAPTER_ROOT/scripts/$script" "$OPENCODE_DIR/$script"
  fi
done

# Fichiers statiques du dashboard web (HTML, CSS, JS).
if [[ -d "$ADAPTER_ROOT/scripts/static" ]]; then
  mkdir -p "$OPENCODE_DIR/static"
  cp -R "$ADAPTER_ROOT/scripts/static/." "$OPENCODE_DIR/static/"
fi

# Registre des fenêtres de contexte (dashboard uniquement, surchargeable).
# Jamais écrasé : un projet qui a sa propre table de modèles la garde.
if [[ -e "$SOURCE_ROOT/core/runtime/models.yaml" && ! -e "$OPENCODE_DIR/models.yaml" ]]; then
  cp "$SOURCE_ROOT/core/runtime/models.yaml" "$OPENCODE_DIR/models.yaml"
fi

# Le gestionnaire de services aussi (séparé du moteur d'agents).
if [[ -e "$ADAPTER_ROOT/scripts/services.py" ]]; then
  cp "$ADAPTER_ROOT/scripts/services.py" "$OPENCODE_DIR/services.py"
fi

# Le dossier personnel OpenCode (~/.opencode/agent) reflete le projet :
# OpenCode y lit le `model:` de chaque agent au lancement.
if command -v python3 >/dev/null 2>&1; then
  python3 "$SOURCE_ROOT/engine/agent_sync.py" --project "$PROJECT_PATH" --core "$SOURCE_ROOT" || true
fi

echo "Sayrhazi installé dans: $PROJECT_PATH"
echo "À compléter: $OPENCODE_DIR/sayrhazi.yaml"
echo "Les rapports, historiques et règles existants n'ont pas été écrasés."
