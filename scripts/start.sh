#!/usr/bin/env bash
# Entwicklungs-Start-Skript
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "${SCRIPT_DIR}")"

cd "${APP_DIR}"

if [[ ! -f ".env" ]]; then
    echo "Keine .env Datei gefunden! Kopiere .env.example:"
    cp .env.example .env
    echo "Bitte .env mit EVE SSO Daten befüllen."
    exit 1
fi

VENV_DIR=".venv"
if [[ -d "venv" && ! -d "${VENV_DIR}" ]]; then
    VENV_DIR="venv"
fi

if [[ ! -d "${VENV_DIR}" ]]; then
    echo "Erstelle Virtual Environment..."
    python3 -m venv "${VENV_DIR}"
    "./${VENV_DIR}/bin/pip" install -r requirements.txt
fi

echo "Starte PlanetFlow (Entwicklung)..."
"./${VENV_DIR}/bin/uvicorn" app.main:app --host 0.0.0.0 --port 8000 --reload
