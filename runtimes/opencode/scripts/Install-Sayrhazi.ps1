[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ProjectPath
)

$ErrorActionPreference = 'Stop'
$resolvedProject = [System.IO.Path]::GetFullPath($ProjectPath)
$sourceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
$adapterRoot = Join-Path $sourceRoot 'runtimes/opencode'

if (-not (Test-Path -LiteralPath $resolvedProject -PathType Container)) {
    throw "Le projet cible n'existe pas : $resolvedProject"
}

$opencode = Join-Path $resolvedProject '.opencode'
$agentTarget = Join-Path $opencode 'agent'
$directories = @(
    $agentTarget,
    (Join-Path $opencode 'resume'),
    (Join-Path $opencode 'history'),
    (Join-Path $opencode 'features'),
    (Join-Path $opencode 'state'),
    (Join-Path $opencode 'backup')
)

foreach ($directory in $directories) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

# Les agents sont contrôlés par Sayrhazi : ils sont mis à jour avec -Force.
$agentSource = Join-Path $adapterRoot 'agents'
Copy-Item -Path (Join-Path $agentSource '*.md') -Destination $agentTarget -Force

# Les fichiers du projet ne sont jamais écrasés automatiquement.
$projectRules = Join-Path $resolvedProject 'AGENTS.md'
if (-not (Test-Path -LiteralPath $projectRules)) {
    Copy-Item (Join-Path $adapterRoot 'templates/AGENTS.md') $projectRules
}

$config = Join-Path $opencode 'sayrhazi.yaml'
if (-not (Test-Path -LiteralPath $config)) {
    Copy-Item (Join-Path $adapterRoot 'templates/sayrhazi.yaml') $config
}

$opencodeJson = Join-Path $opencode 'opencode.json'
if (-not (Test-Path -LiteralPath $opencodeJson)) {
    Copy-Item (Join-Path $adapterRoot 'templates/opencode.json') $opencodeJson
}

$workflow = Join-Path $opencode 'workflow.yaml'
if (-not (Test-Path -LiteralPath $workflow)) {
    Copy-Item (Join-Path $adapterRoot 'templates/workflow.yaml') $workflow
}

# Le watcher est contrôlé par Sayrhazi : il est mis à jour avec -Force.
$watcherSource = Join-Path $adapterRoot 'scripts/watch-work.py'
if (Test-Path -LiteralPath $watcherSource) {
    Copy-Item $watcherSource (Join-Path $opencode 'watch-work.py') -Force
}

# Le dashboard TUI aussi (lecture seule, même cycle de vie que le watcher).
$tuiSource = Join-Path $adapterRoot 'scripts/watch-tui.py'
if (Test-Path -LiteralPath $tuiSource) {
    Copy-Item $tuiSource (Join-Path $opencode 'watch-tui.py') -Force
}

# Registre des fenêtres de contexte (dashboard uniquement, surchargeable).
# Jamais écrasé : un projet qui a sa propre table de modèles la garde.
$modelsSource = Join-Path $sourceRoot 'core/runtime/models.yaml'
$modelsTarget = Join-Path $opencode 'models.yaml'
if ((Test-Path -LiteralPath $modelsSource) -and -not (Test-Path -LiteralPath $modelsTarget)) {
    Copy-Item $modelsSource $modelsTarget
}

# Le gestionnaire de services aussi (séparé du moteur d'agents).
$servicesSource = Join-Path $adapterRoot 'scripts/services.py'
if (Test-Path -LiteralPath $servicesSource) {
    Copy-Item $servicesSource (Join-Path $opencode 'services.py') -Force
}
Write-Host "Sayrhazi installe dans : $resolvedProject"
    Write-Host "A completer avant usage : $config"
Write-Host "Aucun conteneur Docker n'a ete cree. Docker reste une decision du projet applicatif."
