@echo off
rem Evening data refresh (bhavcopy prices, indices, corporate actions, announcements, regime, features) so the
rem next morning's 09:30 board ranks on current data. Started by the Windows task "FinanceBrain Daily Refresh".
cd /d "%~dp0.."
set PYTHONPATH=src
if not exist data\paper_reports mkdir data\paper_reports
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set D=%%i
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['daily']))" >> data\paper_reports\refresh-%D%.log 2>&1
