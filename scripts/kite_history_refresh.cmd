@echo off
rem Incremental historical minute refresh after the owner completes today's Kite login.
rem Default: the last 45 calendar days for each year's top 300 liquid names.
rem No broker orders. Do not schedule without reviewing data-use terms and runtime.
cd /d "%~dp0.."
set PYTHONPATH=src
if not exist .venv\Scripts\python.exe (
    echo Financial Brain virtual environment is missing.
    exit /b 1
)
if not exist data\paper_reports mkdir data\paper_reports
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set D=%%i
for /f %%i in ('powershell -NoProfile -Command "(Get-Date).AddDays(-45).ToString('yyyy-MM-dd')"') do set START=%%i
.venv\Scripts\python.exe -c "from financial_brain.cli import main; import sys; sys.exit(main(['kite-history', 'minute', '--top', '300', '--start', '%START%']))" >> data\paper_reports\kite-history-%D%.log 2>&1
if errorlevel 1 (
    echo Kite history refresh failed or needs today's login. See data\paper_reports\kite-history-%D%.log
    exit /b 1
)
echo Kite history refresh completed. See data\paper_reports\kite-history-%D%.log
