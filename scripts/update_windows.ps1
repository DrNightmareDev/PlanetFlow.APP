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

$projectPath = (Resolve-Path $ProjectRoot).Path
$venvPython = Join-Path $projectPath "$VenvDir\Scripts\python.exe"
$venvPip = Join-Path $projectPath "$VenvDir\Scripts\pip.exe"
$composeFile = Join-Path $projectPath "docker-compose.yml"

Write-Info "Updating repository to origin/$Branch..."
git -C $projectPath fetch origin $Branch
git -C $projectPath reset --hard "origin/$Branch"
Write-Ok "Repository updated"

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
