[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$Branch = "main",
    [string]$VenvDir = ".venv",
    [switch]$Compose
)

$ErrorActionPreference = "Stop"

function Write-Info($Message) { Write-Host "[INFO]  $Message" -ForegroundColor Cyan }
function Write-Ok($Message) { Write-Host "[OK]    $Message" -ForegroundColor Green }
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
$venvPython = Join-Path $projectPath "$VenvDir\Scripts\python.exe"
$venvPip = Join-Path $projectPath "$VenvDir\Scripts\pip.exe"
$composeFile = Join-Path $projectPath "docker-compose.yml"
$envPath = Join-Path $projectPath ".env"

Write-Info "Updating repository to origin/$Branch..."
git -C $projectPath fetch origin $Branch
git -C $projectPath reset --hard "origin/$Branch"
Write-Ok "Repository updated"

if (-not (Test-Path $envPath)) {
    Copy-Item (Join-Path $projectPath ".env.example") $envPath
    Write-Info ".env created from .env.example"
}

Ensure-EnvKey $envPath "RABBITMQ_USER" "planetflow"
Ensure-EnvKey $envPath "RABBITMQ_PASS" "change_me_rabbit"
Ensure-EnvKey $envPath "CELERY_BROKER_URL" "amqp://planetflow:change_me_rabbit@rabbitmq:5672//"
Ensure-EnvKey $envPath "WEB_WORKERS" "2"
Ensure-EnvKey $envPath "SENTRY_DSN" ""
Ensure-EnvKey $envPath "FLOWER_USER" "admin"
Ensure-EnvKey $envPath "FLOWER_PASS" "change_me_flower"
Ensure-EnvKey $envPath "NGINX_PORT" "80"
Ensure-EnvScope $envPath "esi-fittings.read_fittings.v1"

if ($Compose) {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "docker was not found."
    }
    if (-not (Test-Path $composeFile)) {
        throw "docker-compose.yml was not found: $composeFile"
    }

    Write-Info "Running Docker Compose update..."
    try {
        docker compose -f $composeFile pull
    } catch {
        Write-Warning "docker compose pull failed or no remote image is available. Continuing with local build."
    }
    docker compose -f $composeFile build
    docker compose -f $composeFile up -d
    docker compose -f $composeFile exec app alembic upgrade head
    Write-Ok "Docker Compose update completed"

    Write-Host ""
    Write-Host "Log check: docker compose -f $composeFile logs -n 100 app" -ForegroundColor Cyan
    exit 0
}

if (-not (Test-Path $venvPython)) {
    throw "Python virtual environment was not found: $venvPython"
}

Write-Info "Updating Python dependencies..."
& $venvPython -m pip install --upgrade pip
& $venvPip install -r (Join-Path $projectPath "requirements.txt")
Write-Ok "Dependencies updated"

Write-Info "Running database migrations..."
& $venvPython -m alembic upgrade head
Write-Ok "Migrations completed"

Write-Host ""
Write-Host "Windows update completed." -ForegroundColor Green
Write-Host "Restart the app or service if it is currently running." -ForegroundColor Cyan
