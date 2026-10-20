#!/usr/bin/env bash
# =====================================================================
# Paylane AI Support Agent - Startup Script (Unix / macOS / Linux)
# =====================================================================

set -e

# Activate virtual environment if present
if [ -d "venv" ]; then
    echo "Activating virtual environment (venv)..."
    source venv/bin/activate
elif [ -d ".venv" ]; then
    echo "Activating virtual environment (.venv)..."
    source .venv/bin/activate
fi

# Check for .env file
if [ ! -f ".env" ]; then
    echo "[WARNING] .env file not found. Copying .env.example to .env..."
    cp .env.example .env
    echo "Please edit .env and configure your GROQ_API_KEY before running."
fi

PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"

echo "Starting Paylane Support Agent on http://${HOST}:${PORT}..."
echo "Swagger documentation available at http://${HOST}:${PORT}/docs"
echo "Interactive Demo UI available at http://${HOST}:${PORT}/"

exec uvicorn paylane_api.main:app --host "$HOST" --port "$PORT" --reload
