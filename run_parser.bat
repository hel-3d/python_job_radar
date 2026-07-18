@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found:
    echo %~dp0.venv
    exit /b 1
)

if not exist "run_parser.py" (
    echo File run_parser.py not found.
    exit /b 1
)

".venv\Scripts\python.exe" "run_parser.py"