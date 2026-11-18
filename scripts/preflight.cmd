@echo off
rem Pre-open health check (weekdays 08:30 via the "FinanceBrain Preflight" task): network, Kite login, disk, research
rem database, data contracts. Read-only. Sends an alert if today's paper session may not run.
cd /d "%~dp0.."
set PYTHONPATH=src
if not exist data\paper_reports mkdir data\paper_reports
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set D=%%i
.venv\Scripts\python.exe -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['preflight']))" >> data\paper_reports\preflight-%D%.log 2>&1
