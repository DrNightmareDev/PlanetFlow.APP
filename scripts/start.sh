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

if [[ ! -d "venv" ]]; then
    echo "Erstelle Virtual Environment..."
    python3 -m venv venv
    ./venv/bin/pip install -r requirements.txt
fi

echo "Starte EVE PI Manager (Entwicklung)..."
./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
