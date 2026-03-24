[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$Branch = "main",
    [string]$VenvDir = ".venv"
)

$ErrorActionPreference = "Stop"

function Write-Info($msg) { Write-Host "[INFO]  $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "[OK]    $msg" -ForegroundColor Green }

$projectPath = (Resolve-Path $ProjectRoot).Path
$venvPython = Join-Path $projectPath "$VenvDir\Scripts\python.exe"
$venvPip = Join-Path $projectPath "$VenvDir\Scripts\pip.exe"

Write-Info "Aktualisiere Repository auf $Branch..."
git -C $projectPath fetch origin $Branch
git -C $projectPath reset --hard "origin/$Branch"
Write-Ok "Repository aktualisiert"

Write-Info "Aktualisiere Python-Abhaengigkeiten..."
& $venvPython -m pip install --upgrade pip
& $venvPip install -r (Join-Path $projectPath "requirements.txt")
Write-Ok "Abhaengigkeiten aktualisiert"

Write-Info "Fuehre Migrationen aus..."
& $venvPython -m alembic upgrade head
Write-Ok "Migrationen abgeschlossen"

Write-Host ""
Write-Host "Windows-Update abgeschlossen." -ForegroundColor Green
Write-Host "App danach neu starten, falls sie laeuft." -ForegroundColor Cyan
