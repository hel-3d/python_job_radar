@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found:
    echo %~dp0.venv
    exit /b 1
)

if not exist "run_ai_reviewer.py" (
    echo File run_ai_reviewer.py not found.
    exit /b 1
)

".venv\Scripts\python.exe" "run_ai_reviewer.py"