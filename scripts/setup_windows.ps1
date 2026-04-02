[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$VenvDir = ".venv"
)

$ErrorActionPreference = "Stop"

function Write-Info($msg) { Write-Host "[INFO]  $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "[OK]    $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[WARN]  $msg" -ForegroundColor Yellow }
function Ensure-EnvKey($Path, $Key, $Value) {
    if (-not (Test-Path $Path)) { return }
    $content = Get-Content $Path
    if ($content -notmatch "^$([regex]::Escape($Key))=") {
        Add-Content -Path $Path -Value "$Key=$Value"
        Write-Ok ".env: added $Key"
    }
}
function Ensure-EnvScope($Path, $Scope) {
    if (-not (Test-Path $Path)) { return }
    $content = Get-Content $Path -Raw
    $match = [regex]::Match($content, '(?m)^EVE_SCOPES=(.*)$')
    if (-not $match.Success) { return }
    $current = $match.Groups[1].Value.Trim()
    if ((" " + $current + " ").Contains(" $Scope ")) { return }
    $updated = ($current + " " + $Scope).Trim()
    $content = [regex]::Replace($content, '(?m)^EVE_SCOPES=.*$', "EVE_SCOPES=$updated")
    Set-Content -Path $Path -Value $content
    Write-Ok ".env: added missing scope $Scope"
}

$projectPath = (Resolve-Path $ProjectRoot).Path
$venvPath = Join-Path $projectPath $VenvDir
$python = Get-Command py -ErrorAction SilentlyContinue
$envPath = Join-Path $projectPath ".env"

if (-not $python) {
    throw "Python Launcher 'py' nicht gefunden. Bitte Python 3.11+ installieren."
}

Write-Info "Projektpfad: $projectPath"

if (-not (Test-Path $envPath)) {
    Copy-Item (Join-Path $projectPath ".env.example") $envPath
    Write-Warn ".env wurde aus .env.example erstellt. Bitte EVE_CLIENT_ID, EVE_CLIENT_SECRET und DATABASE_URL ausfuellen."
}

Ensure-EnvKey $envPath "RABBITMQ_USER" "planetflow"
Ensure-EnvKey $envPath "RABBITMQ_PASS" "change_me_rabbit"
Ensure-EnvKey $envPath "CELERY_BROKER_URL" "amqp://planetflow:change_me_rabbit@rabbitmq:5672//"
Ensure-EnvKey $envPath "COOKIE_SECURE" "false"
Ensure-EnvKey $envPath "WEB_WORKERS" "2"
Ensure-EnvKey $envPath "SENTRY_DSN" ""
Ensure-EnvKey $envPath "FLOWER_USER" "admin"
Ensure-EnvKey $envPath "FLOWER_PASS" "change_me_flower"
Ensure-EnvKey $envPath "NGINX_PORT" "80"
# Ensure all required ESI scopes are present:
#   esi-planets.manage_planets.v1          - read/write PI colonies
#   esi-planets.read_customs_offices.v1    - customs office tax rates
#   esi-location.read_location.v1          - character location (route planner)
#   esi-characters.read_corporation_roles.v1 - corp-manager access checks
#   esi-skills.read_skills.v1              - skill queue / SP display
#   esi-fittings.read_fittings.v1          - fittings comparison page
$requiredScopes = @(
    "esi-planets.manage_planets.v1",
    "esi-planets.read_customs_offices.v1",
    "esi-location.read_location.v1",
    "esi-characters.read_corporation_roles.v1",
    "esi-skills.read_skills.v1",
    "esi-fittings.read_fittings.v1"
)
foreach ($scope in $requiredScopes) { Ensure-EnvScope $envPath $scope }

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
Write-Host "     .\$VenvDir\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 80"
Write-Host "     (Port 80 erfordert ggf. Admin-Rechte. Alternativ: --port 8000 fuer lokale Entwicklung)"
Write-Host ""
Write-Host "Optional mit winget:" -ForegroundColor Cyan
Write-Host "  winget install Python.Python.3.11"
Write-Host "  winget install PostgreSQL.PostgreSQL"
