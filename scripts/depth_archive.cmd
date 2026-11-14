@echo off
rem Market-hours depth and open-interest archive (weekdays 09:10 via the "FinanceBrain Depth Archive" task).
rem Full quotes - five levels of the book, OI, VWAP - for every F&O stock, its near future and the index options near
rem the money, once a minute, into data\depth_archive.duckdb. Read-only against Kite: it never places an order.
rem Waits for the day's Kite login (scripts\kite_login.cmd) until 10:30, exits by itself on a holiday and at the close,
rem and refuses to start twice. Check it with: fb depth-status
cd /d "%~dp0.."
set PYTHONPATH=src
if not exist data\paper_reports mkdir data\paper_reports
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set D=%%i
.venv\Scripts\python.exe -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['depth-archive']))" >> data\paper_reports\depth-archive-%D%.log 2>&1
