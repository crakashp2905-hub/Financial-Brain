@echo off
setlocal
rem Scheduled paper-book close and evidence export. This does not contact a broker or create orders.
rem Set FB_DAILY_CYCLE_MANIFEST in the Task Scheduler action environment to an owner-managed manifest.
cd /d "%~dp0.."
set "PYTHONPATH=%CD%\src"
set "MANIFEST=%FB_DAILY_CYCLE_MANIFEST%"
if not defined MANIFEST (
  echo FB_DAILY_CYCLE_MANIFEST is required.
  exit /b 64
)
if not exist "%MANIFEST%" (
  echo Daily-cycle manifest not found: "%MANIFEST%"
  exit /b 64
)
if not exist ".venv\Scripts\python.exe" (
  echo Repository virtual environment is missing: .venv\Scripts\python.exe
  exit /b 64
)
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "D=%%i"
if not exist "data\paper_reports" mkdir "data\paper_reports"
set "LOG=data\paper_reports\daily-cycle-%D%.log"
echo [%DATE% %TIME%] Preflight manifest "%MANIFEST%" >> "%LOG%"
".venv\Scripts\python.exe" -m financial_brain.forward_edge.cli daily-paper-preflight --manifest "%MANIFEST%" >> "%LOG%" 2>&1
if errorlevel 1 exit /b %ERRORLEVEL%
echo [%DATE% %TIME%] Finalize paper books >> "%LOG%"
".venv\Scripts\python.exe" -m financial_brain.forward_edge.cli daily-paper-cycle --manifest "%MANIFEST%" >> "%LOG%" 2>&1
exit /b %ERRORLEVEL%
