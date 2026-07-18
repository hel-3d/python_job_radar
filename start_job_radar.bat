@echo off
cd /d "%~dp0"

start "" /B "%~dp0run_parser.bat"
timeout /t 2 /nobreak >nul

start "" /B "%~dp0run_ai_reviewer.bat"
timeout /t 2 /nobreak >nul

start "" /B "%~dp0run_upwork_parser.bat"

exit /b 0