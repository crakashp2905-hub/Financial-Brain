@echo off
rem Five-desk nightly SHADOW run, after the close (suggested 16:30 IST on weekdays). Paper only.
rem Replays the legacy live-v1 and paper-book ledgers read-only into the five-desk ledger, flags any
rem live trade with no exit (exit code 2) and writes data\paper_reports\five-desk-<day>.html/.json.
rem The owner registers the Windows task; Claude cannot.
cd /d "%~dp0.."
set PYTHONPATH=src
if not exist data\paper_reports mkdir data\paper_reports
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set D=%%i
.venv\Scripts\python.exe scripts\five_desk_nightly.py --root "%CD%" --kite >> data\paper_reports\five-desk-%D%.log 2>&1
if %ERRORLEVEL%==2 (
    powershell -NoProfile -Command "New-BurntToastNotification -Text 'Finance-Brain', 'Live paper trades without an exit - see five-desk report' " 2>nul
    echo Live paper trades without an exit. See data\paper_reports\five-desk-%D%.json
)
exit /b %ERRORLEVEL%
