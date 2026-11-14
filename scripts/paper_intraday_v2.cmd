@echo off
rem The paper live-v2 cohort: live-v1 plus the skip rules a person approved (fb learn approve), in its own ledger, beside the
rem unchanged live-v1 session. Started by the weekday 09:06 task "FinanceBrain Paper Intraday v2" (register it once, only when a rule is
rem ACTIVE; scripts\register_paper_v2_task.ps1). It exits at once, doing nothing, when no skip rule is active. Paper only: no order can
rem be placed. Compare it with live-v1: fb learn ab
cd /d "%~dp0.."
set PYTHONPATH=src
if not exist data\paper_reports mkdir data\paper_reports
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set D=%%i
.venv\Scripts\python.exe -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['paper-intraday', '--v2']))" >> data\paper_reports\runner-v2-%D%.log 2>&1
