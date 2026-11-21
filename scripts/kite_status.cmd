@echo off
cd /d "%~dp0.."
set PYTHONPATH=src
.venv\Scripts\python.exe -c "from financial_brain import env; env.load(); from financial_brain.cli import main; main(['kite','status'])" | findstr /i "access"
