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

# Moteur + briques partagées + serveur unifié (cohérent avec engine/updater.py).
foreach ($script in @('watch-work.py', 'services.py', 'sayrhazi-modules.py', 'sayrhazi-serveur.py')) {
    $src = Join-Path $adapterRoot "scripts/$script"
    if (Test-Path -LiteralPath $src) {
        Copy-Item $src (Join-Path $opencode $script) -Force
    }
}

# Fichiers statiques du dashboard web (HTML, CSS, JS).
$staticSource = Join-Path $adapterRoot 'scripts/static'
$staticTarget = Join-Path $opencode 'static'
if (Test-Path -LiteralPath $staticSource) {
    New-Item -ItemType Directory -Path $staticTarget -Force | Out-Null
    Copy-Item (Join-Path $staticSource '*') $staticTarget -Recurse -Force
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

# Le dossier personnel OpenCode (~/.opencode/agent) reflete le projet :
# OpenCode y lit le `model:` de chaque agent au lancement.
try {
    python (Join-Path $sourceRoot 'engine\agent_sync.py') --project $resolvedProject --core $sourceRoot
} catch {
    Write-Host "Avertissement : synchronisation de ~/.opencode/agent impossible ($($_.Exception.Message))" -ForegroundColor Yellow
}
Write-Host "Sayrhazi installe dans : $resolvedProject"
    Write-Host "A completer avant usage : $config"
Write-Host "Aucun conteneur Docker n'a ete cree. Docker reste une decision du projet applicatif."
