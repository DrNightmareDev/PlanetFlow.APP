[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$VenvDir = ".venv"
)

$ErrorActionPreference = "Stop"

function Write-Info($msg) { Write-Host "[INFO]  $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "[OK]    $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[WARN]  $msg" -ForegroundColor Yellow }

$projectPath = (Resolve-Path $ProjectRoot).Path
$venvPath = Join-Path $projectPath $VenvDir
$python = Get-Command py -ErrorAction SilentlyContinue

if (-not $python) {
    throw "Python Launcher 'py' nicht gefunden. Bitte Python 3.11+ installieren."
}

Write-Info "Projektpfad: $projectPath"

if (-not (Test-Path (Join-Path $projectPath ".env"))) {
    Copy-Item (Join-Path $projectPath ".env.example") (Join-Path $projectPath ".env")
    Write-Warn ".env wurde aus .env.example erstellt. Bitte EVE_CLIENT_ID, EVE_CLIENT_SECRET und DATABASE_URL ausfuellen."
}

if (-not (Test-Path $venvPath)) {
    Write-Info "Erstelle Virtual Environment..."
    & py -3 -m venv $venvPath
    Write-Ok "Virtual Environment erstellt"
}

$venvPython = Join-Path $venvPath "Scripts\python.exe"
$venvPip = Join-Path $venvPath "Scripts\pip.exe"

Write-Info "Installiere Python-Abhaengigkeiten..."
& $venvPython -m pip install --upgrade pip
& $venvPip install -r (Join-Path $projectPath "requirements.txt")
Write-Ok "Python-Abhaengigkeiten installiert"

Write-Info "Fuehre Alembic-Migrationen aus..."
& $venvPython -m alembic upgrade head
Write-Ok "Migrationen abgeschlossen"

Write-Host ""
Write-Host "Windows-Setup abgeschlossen." -ForegroundColor Green
Write-Host ""
Write-Host "Naechste Schritte:" -ForegroundColor Cyan
Write-Host "  1. PostgreSQL muss lokal laufen und DATABASE_URL in .env gesetzt sein."
Write-Host "  2. EVE SSO Werte in .env eintragen."
Write-Host "  3. Starten mit:"
Write-Host "     .\$VenvDir\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
Write-Host ""
Write-Host "Optional mit winget:" -ForegroundColor Cyan
Write-Host "  winget install Python.Python.3.11"
Write-Host "  winget install PostgreSQL.PostgreSQL"
