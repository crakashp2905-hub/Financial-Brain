@echo off
rem Evening refresh, weekdays 18:45 (Windows task "FinanceBrain Daily Refresh"). All read-only market data.
rem 1. prices, indices, actions, announcements, regime, features - so the next 09:30 board ranks on current data
rem 1b. NSE delivery position (MTO) for today: lake, then the delivery table
rem 1c. NSE structured insider disclosures (PIT XBRL) for yesterday and today: lake, then insider_disclosures
rem 1d. free datasets (2026-10-18): F&O bhavcopy, ban list, participant OI/volume; shareholding filings of the last 3 days;
rem     bulk/block deals and short selling (this month); NSE corporate actions, SAST Reg 29, pledges, encumbrance, event
rem     calendar, IPOs; FII/DII flows and SEBI RSS; FRED + MoSPI macro; index total return + constituent lists;
rem     rating letters (300 a night, oldest backlog last), rating directions, monthly business updates, call transcripts
rem 2. champion/challenger: record today's locked out-of-sample picks, resolve matured cohorts
rem 3. F&O snapshot: futures basis and OI, option-chain statistics, day-over-day positioning (needs the Kite token)
rem 4. loss review: situations that keep losing become PROPOSED hypotheses (never applied automatically)
rem 5. the paper books (position, swing, learned model, momentum, ranking v2) and the zoo (every tested daily strategy), then fb oos (ADR-0004 trials against their backtest band)
rem    both suggestion books (top-5 position, top-50 swing): sell what hit its stop or horizon, buy today's names at the next close
rem 6. your Kite holdings, positions and today's trade book (read only, stored in data\my_portfolio.duckdb)
rem 7. the terminal page, data\terminal\index.html
rem 8. desktop alerts: a holding exiting by rule, a name clearing 1 percent net, a failed paper run
cd /d "%~dp0.."
set PYTHONPATH=src
if not exist data\paper_reports mkdir data\paper_reports
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set D=%%i
set LOG=data\paper_reports\refresh-%D%.log
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['daily']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['delivery', '--start', '%D%', '--prefetch', '--workers', '1']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['delivery', '--start', '%D%']))" >> %LOG% 2>&1
for /f %%i in ('powershell -NoProfile -Command "(Get-Date).AddDays(-1).ToString('yyyy-MM-dd')"') do set Y=%%i
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['insider', '--start', '%Y%', '--end', '%D%', '--prefetch']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['insider', '--start', '%Y%', '--end', '%D%']))" >> %LOG% 2>&1
for /f %%i in ('powershell -NoProfile -Command "(Get-Date).AddDays(-31).ToString('yyyy-MM-dd')"') do set M=%%i
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['nse-archives', '--start', '%Y%', '--end', '%D%', '--prefetch']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['nse-archives', '--start', '%Y%', '--end', '%D%']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['shareholding', '--prefetch']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['shareholding']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['bulk-deals', '--start', '%M%', '--end', '%D%', '--prefetch']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['bulk-deals', '--start', '%M%', '--end', '%D%']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['disclosures', '--start', '%M%', '--prefetch']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['disclosures', '--since', '%M%']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['flows']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['rating-docs', '--limit', '300']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['rating-actions']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['monthly-updates']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['calls-backfill', '--start', '%Y%', '--end', '%D%']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['macro']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['index-tri', '--start', '%M%']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['champion', '--record']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['fno-snapshot']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['loss-review', '--propose']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['book']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['zoo']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['oos']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['my-portfolio', '--sync']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['terminal']))" >> %LOG% 2>&1
python -c "from financial_brain import env; env.load(); from financial_brain.cli import main; import sys; sys.exit(main(['alerts']))" >> %LOG% 2>&1
