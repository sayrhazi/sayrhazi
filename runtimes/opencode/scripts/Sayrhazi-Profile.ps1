# Sayrhazi-Profile.ps1 — fonctions de profil Sayrhazi (versionné dans le dépôt).
#
# Sourcing (une fois, dans $PROFILE) — remplace tout bloc Sayrhazi existant :
#   $env:SAYRHAZI_HOME = "C:\chemin\vers\Sayrhazi"
#   . "$env:SAYRHAZI_HOME\runtimes\opencode\scripts\Sayrhazi-Profile.ps1"
#
# Fonctions : Sayrhazi, tunda, update, info, remove
#             (+ Sayrhazi-Install/Check/Update).
# Les flags (-*, ex. --version) vont au CLI ; un chemin va à l'installeur.
# Bash sensible à la casse : `Sayrhazi` (fonction) et `sayrhazi` (bin npm) coexistent.

$env:PYTHONIOENCODING = "utf-8"
try { chcp 65001 | Out-Null } catch {}
try { [console]::InputEncoding = [console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

function Sayrhazi-Install {
    param([string]$ProjectPath = ".")
    & "$env:SAYRHAZI_HOME\runtimes\opencode\scripts\Install-Sayrhazi.ps1" -ProjectPath $ProjectPath
}

function Sayrhazi-Check {
    param([string]$ProjectPath = ".")
    python "$env:SAYRHAZI_HOME\engine\checker.py" $ProjectPath
}

function Sayrhazi-Update {
    param([string]$ProjectPath = ".")
    python "$env:SAYRHAZI_HOME\engine\updater.py" --project $ProjectPath
    if ($LASTEXITCODE -ne 0) { return }
    Sayrhazi-Check $ProjectPath
}

function tunda {
    param([string]$Sub = "sayrhazi", [string]$ProjectPath = ".")
    if ($Sub -ne "sayrhazi") { Write-Host "Usage: tunda sayrhazi" -ForegroundColor Yellow; return }
    python "$env:SAYRHAZI_HOME\cli\commands\check.py" $Sub --project $ProjectPath
}

function update {
    param([string]$Sub = "sayrhazi", [string]$ProjectPath = ".")
    if ($Sub -ne "sayrhazi") { Write-Host "Usage: update sayrhazi" -ForegroundColor Yellow; return }
    Sayrhazi-Update $ProjectPath
}

function info {
    param([string]$Sub = "sayrhazi", [string]$ProjectPath = ".")
    if ($Sub -ne "sayrhazi") { Write-Host "Usage: info sayrhazi" -ForegroundColor Yellow; return }
    python "$env:SAYRHAZI_HOME\cli\main.py" info --project $ProjectPath
}

function remove {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
    $path = "."
    $yes = $false
    foreach ($a in $Args) {
        if ($a -eq "--yes") { $yes = $true }
        elseif ($a -eq "sayrhazi") { }
        elseif ($path -eq ".") { $path = $a }
        else { Write-Host "Usage: remove sayrhazi [chemin] [--yes]" -ForegroundColor Yellow; return }
    }
    python "$env:SAYRHAZI_HOME\cli\main.py" remove --project $path
    if ($LASTEXITCODE -eq 3) { return }
    if ($LASTEXITCODE -ne 0) { return }
    if (-not $yes) {
        $ans = Read-Host "Supprimer vraiment ces fichiers ? (tapez yes)"
        if ($ans -ne "yes") { Write-Host "Annule, rien n'a ete supprime."; return }
    }
    python "$env:SAYRHAZI_HOME\cli\main.py" remove --project $path --yes
}

function Sayrhazi {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
    if ($Args.Count -ge 1 -and $Args[0] -like "-*") {
        python "$env:SAYRHAZI_HOME\cli\main.py" @Args
        return
    }
    $sub = ""
    $path = "."
    if ($Args.Count -ge 1) {
        if ($Args[0] -in @("check", "tunda", "info", "update", "remove", "install", "server")) {
            $sub = $Args[0]
            if ($Args.Count -ge 2) { $path = $Args[1] }
        } else {
            $path = $Args[0]
        }
    }
    if ($sub -eq "check") { Sayrhazi-Check $path }
    elseif ($sub -eq "tunda") { python "$env:SAYRHAZI_HOME\cli\main.py" tunda --project $path }
    elseif ($sub -eq "info") { python "$env:SAYRHAZI_HOME\cli\main.py" info --project $path }
    elseif ($sub -eq "update") { Sayrhazi-Update $path }
    elseif ($sub -eq "remove") {
        $yes = $Args -contains "--yes"
        python "$env:SAYRHAZI_HOME\cli\main.py" remove --project $path
        if ($LASTEXITCODE -eq 3) { return }
        if ($LASTEXITCODE -ne 0) { return }
        if (-not $yes) {
            $ans = Read-Host "Supprimer vraiment ces fichiers ? (tapez yes)"
            if ($ans -ne "yes") { Write-Host "Annule, rien n'a ete supprime."; return }
        }
        python "$env:SAYRHAZI_HOME\cli\main.py" remove --project $path --yes
    }
    elseif ($sub -eq "server") {
        python "$env:SAYRHAZI_HOME\cli\main.py" server --root $path
    }
    elseif ($sub -eq "install") {
        python "$env:SAYRHAZI_HOME\engine\banner.py" sayrhazi
        Sayrhazi-Install $path
        if (-not $?) { Write-Host "Installation echouee." -ForegroundColor Red; return }
        Sayrhazi-Check $path
    }
    else {
        python "$env:SAYRHAZI_HOME\engine\banner.py" sayrhazi
        Sayrhazi-Install $path
        if (-not $?) {
            Write-Host "Installation echouee." -ForegroundColor Red
            return
        }
        Sayrhazi-Check $path
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Install OK, mais config incomplete : complete .opencode/sayrhazi.yaml (A_COMPLETER restant)." -ForegroundColor Yellow
        }
    }
}

