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

# Le watcher est contrôlé par Sayrhazi : il est mis à jour à chaque install.
if [[ -e "$ADAPTER_ROOT/scripts/watch-work.py" ]]; then
  cp "$ADAPTER_ROOT/scripts/watch-work.py" "$OPENCODE_DIR/watch-work.py"
fi

# Le dashboard TUI aussi (lecture seule, même cycle de vie que le watcher).
if [[ -e "$ADAPTER_ROOT/scripts/watch-tui.py" ]]; then
  cp "$ADAPTER_ROOT/scripts/watch-tui.py" "$OPENCODE_DIR/watch-tui.py"
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

echo "Sayrhazi installé dans: $PROJECT_PATH"
echo "À compléter: $OPENCODE_DIR/sayrhazi.yaml"
echo "Les rapports, historiques et règles existants n'ont pas été écrasés."
