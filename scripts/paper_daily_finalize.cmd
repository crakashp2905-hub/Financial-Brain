@echo off
setlocal
rem Scheduled paper-book close and evidence export. This does not contact a broker or create orders.
rem Set FB_DAILY_CYCLE_CONFIG to generate a manifest from verified runner close receipts.
rem FB_DAILY_CYCLE_MANIFEST remains available for an explicitly prepared manifest.
cd /d "%~dp0.."
set "PYTHONPATH=%CD%\src"
if not exist ".venv\Scripts\python.exe" (
  echo Repository virtual environment is missing: .venv\Scripts\python.exe
  exit /b 64
)
for /f %%i in ('powershell -NoProfile -Command "[TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTimeOffset]::UtcNow,'India Standard Time').ToString('yyyy-MM-dd')"') do set "D=%%i"
if not exist "data\paper_reports" mkdir "data\paper_reports"
set "LOG=data\paper_reports\daily-cycle-%D%.log"
if defined FB_DAILY_CYCLE_CONFIG goto configured_cycle
goto prepared_manifest
:configured_cycle
if not exist "%FB_DAILY_CYCLE_CONFIG%" (
  echo Daily-cycle config not found: "%FB_DAILY_CYCLE_CONFIG%"
  exit /b 64
)
echo [%DATE% %TIME%] Generate, preflight, and finalize from config "%FB_DAILY_CYCLE_CONFIG%" >> "%LOG%"
".venv\Scripts\python.exe" -m financial_brain.forward_edge.cli daily-paper-run --config "%FB_DAILY_CYCLE_CONFIG%" --session-date "%D%" >> "%LOG%" 2>&1
exit /b %ERRORLEVEL%
:prepared_manifest
set "MANIFEST=%FB_DAILY_CYCLE_MANIFEST%"
if not defined MANIFEST (
  echo FB_DAILY_CYCLE_CONFIG or FB_DAILY_CYCLE_MANIFEST is required.
  exit /b 64
)
if not exist "%MANIFEST%" (
  echo Daily-cycle manifest not found: "%MANIFEST%"
  exit /b 64
)
echo [%DATE% %TIME%] Preflight manifest "%MANIFEST%" >> "%LOG%"
".venv\Scripts\python.exe" -m financial_brain.forward_edge.cli daily-paper-preflight --manifest "%MANIFEST%" >> "%LOG%" 2>&1
if errorlevel 1 exit /b %ERRORLEVEL%
echo [%DATE% %TIME%] Finalize paper books >> "%LOG%"
".venv\Scripts\python.exe" -m financial_brain.forward_edge.cli daily-paper-cycle --manifest "%MANIFEST%" >> "%LOG%" 2>&1
exit /b %ERRORLEVEL%
