@echo off
cd /d "%~dp0.."
set PYTHONPATH=src
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; main(['kite','status'])" | findstr /i "access"
