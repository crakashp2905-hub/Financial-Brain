@echo off
rem Live intraday PAPER trading for one session (09:15-15:30 IST). Paper only: no order can be placed.
rem Started by the Windows task "FinanceBrain Paper Intraday" on weekdays; exits by itself on holidays,
rem if the Kite token is missing (log in with scripts\kite_login.cmd), and after the close.
cd /d "%~dp0.."
set PYTHONPATH=src
if not exist data\paper_reports mkdir data\paper_reports
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set D=%%i
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['paper-intraday']))" >> data\paper_reports\runner-%D%.log 2>&1
