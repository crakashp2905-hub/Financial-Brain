@echo off
rem Read-only market-hours stock and option scenario alerts. Requires today's Kite login.
cd /d "%~dp0.."
set PYTHONPATH=src
if not exist data\paper_reports mkdir data\paper_reports
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set D=%%i
.venv\Scripts\python.exe -m financial_brain.signals.watch >> data\paper_reports\watch-%D%.log 2>&1
