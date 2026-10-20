@echo off
REM =====================================================================
REM Paylane AI Support Agent - Windows Startup Script (Batch)
REM =====================================================================

IF EXIST "venv\Scripts\activate.bat" (
    echo Activating virtual environment...
    call venv\Scripts\activate.bat
)

IF NOT EXIST ".env" (
    echo [WARNING] .env file not found. Copying .env.example to .env...
    copy .env.example .env
    echo Please edit .env and configure your GROQ_API_KEY.
)

set PORT=8000
set HOST=0.0.0.0

echo Starting Paylane Support Agent on http://%HOST%:%PORT%...
echo Swagger documentation available at http://%HOST%:%PORT%/docs
echo Interactive Demo UI available at http://%HOST%:%PORT%/

uvicorn paylane_api.main:app --host %HOST% --port %PORT% --reload
