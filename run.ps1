# =====================================================================
# Paylane AI Support Agent - Windows PowerShell Startup Script
# =====================================================================

$ErrorActionPreference = "Stop"

if (Test-Path "venv\Scripts\Activate.ps1") {
    Write-Host "Activating virtual environment..." -ForegroundColor Cyan
    & "venv\Scripts\Activate.ps1"
}

if (-not (Test-Path ".env")) {
    Write-Warning ".env file not found. Copying .env.example to .env..."
    Copy-Item ".env.example" ".env"
    Write-Host "Please configure your GROQ_API_KEY in .env before continuing." -ForegroundColor Yellow
}

$port = if ($env:PORT) { $env:PORT } else { "8000" }
$host_addr = if ($env:HOST) { $env:HOST } else { "0.0.0.0" }

Write-Host "Starting Paylane Support Agent on http://${host_addr}:${port}..." -ForegroundColor Green
Write-Host "Swagger documentation: http://${host_addr}:${port}/docs" -ForegroundColor Cyan
Write-Host "Interactive Demo UI:   http://${host_addr}:${port}/" -ForegroundColor Cyan

uvicorn paylane_api.main:app --host $host_addr --port $port --reload
