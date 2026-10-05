@echo off
rem Evening refresh, weekdays 18:45 (Windows task "FinanceBrain Daily Refresh"). All read-only market data.
rem 1. prices, indices, actions, announcements, regime, features - so the next 09:30 board ranks on current data
rem 2. champion/challenger: record today's locked out-of-sample picks, resolve matured cohorts
rem 3. F&O snapshot: futures basis and OI, option-chain statistics, day-over-day positioning (needs the Kite token)
rem 4. the terminal page, data	erminalindex.html
cd /d "%~dp0.."
set PYTHONPATH=src
if not exist data\paper_reports mkdir data\paper_reports
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set D=%%i
set LOG=data\paper_reports\refresh-%D%.log
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['daily']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['champion', '--record']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['fno-snapshot']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['terminal']))" >> %LOG% 2>&1
