' Runs a .cmd script with no console window, waits for it, and returns its exit code to Task Scheduler.
' Used by the "FinanceBrain Paper Intraday" task so the day's paper run has no window that can be closed by
' accident (on 2026-10-06 a closed console killed the live run at 09:09).
' Usage: wscript.exe run_hidden.vbs "D:\Finance-Brain\scripts\paper_intraday.cmd"
If WScript.Arguments.Count < 1 Then WScript.Quit 2
Set sh = CreateObject("WScript.Shell")
rc = sh.Run("cmd.exe /c """ & WScript.Arguments(0) & """", 0, True)
WScript.Quit rc
