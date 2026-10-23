@echo off
rem One-click daily Kite login. A browser opens on Zerodha: you authenticate there (password + 2FA).
rem Nothing here sees them. The access token is saved to data\kite_access_token.json, which every
rem Financial-Brain session reads. Tokens expire each morning (~6am IST), so this is a daily step.
cd /d "%~dp0.."
set PYTHONPATH=src
if not exist .venv\Scripts\python.exe (
    echo Financial Brain virtual environment is missing. Set it up before Kite login.
    pause
    exit /b 1
)
.venv\Scripts\python.exe -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['kite','login']))"
if errorlevel 1 (
    echo Kite login did not complete. The market watcher will keep waiting for a valid session.
    pause
    exit /b 1
)
.venv\Scripts\python.exe -c "from financial_brain import env; env.load(); from financial_brain.cli import main; main(['kite','status'])" | findstr /i "access token"
pause
