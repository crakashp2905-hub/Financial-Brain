@echo off
rem One-click daily Kite login. A browser opens on Zerodha: you authenticate there (password + 2FA).
rem Nothing here sees them. The access token is saved to data\kite_access_token.json, which every
rem Financial-Brain session reads. Tokens expire each morning (~6am IST), so this is a daily step.
cd /d "%~dp0.."
set PYTHONPATH=src
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['kite','login']))"
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; main(['kite','status'])" | findstr /i "access token"
pause
