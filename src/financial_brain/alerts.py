"""Alerts: tell the owner when something is worth acting on, instead of asking them to watch a page.

Windows desktop notifications, raised locally through PowerShell; nothing leaves the machine. The text is built
only from the system's own numbers and symbols and is passed to PowerShell in environment variables, never pasted
into the script, so no data can become a command. Each alert has a key and is sent once (``data/alerts_sent.json``).

Triggers (all rule-based):

    opportunity     a name's expected net alpha clears CLEAR_NET - rare by design
    thesis          a holding's thesis health falls to EXIT BY RULE, or its price is below the plan's stop
    kill switch     the paper loop's kill switch is not OK
    run failed      today's live paper run did not complete (the 2026-10-06 failure would have been caught)
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import date
from pathlib import Path

SENT = Path("data") / "alerts_sent.json"
CLEAR_NET = 0.01               #: +1% net over the horizon: alerts are for the rare name that clearly clears

_PS = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$x = $t.GetElementsByTagName('text')
$x.Item(0).AppendChild($t.CreateTextNode($env:FB_ALERT_TITLE)) | Out-Null
$x.Item(1).AppendChild($t.CreateTextNode($env:FB_ALERT_BODY)) | Out-Null
$n = [Windows.UI.Notifications.ToastNotification]::new($t)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe').Show($n)
"""


def toast(title: str, body: str) -> bool:
    env = {**os.environ, "FB_ALERT_TITLE": title[:120], "FB_ALERT_BODY": body[:400]}
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS], env=env,
                           capture_output=True, timeout=30)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _sent(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def send(alerts: list[dict], *, notify=toast, path: Path = SENT) -> list[dict]:
    """Send each alert not already sent (by key). Returns the ones sent now."""
    seen = _sent(path)
    out = []
    for a in alerts:
        if a["key"] in seen:
            continue
        if notify(a["title"], a["body"]):
            seen[a["key"]] = date.today().isoformat()
            out.append(a)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seen, indent=1), encoding="utf-8")
    return out


def opportunities(best, day: str) -> list[dict]:
    out = []
    for r in best[best["expected_net"] >= CLEAR_NET].itertuples():
        out.append({"key": f"opp:{day}:{r.ticker}", "title": f"Opportunity: {r.ticker} clears costs",
                    "body": f"{r.type}, {r.horizon} sessions, expected net alpha {r.expected_net * 100:+.2f}% "
                            f"({r.evidence}). Details on the terminal. Information, not advice."})
    return out


def holdings(rows: list[dict], day: str) -> list[dict]:
    """``rows``: {symbol, recommendation, score, reason}."""
    return [{"key": f"thesis:{day}:{r['symbol']}", "title": f"Holding: {r['symbol']} - {r['recommendation']}",
             "body": f"Thesis health {r['score']:.0f}/100. {r['reason']}. Rule-based, not advice."}
            for r in rows if r["recommendation"] == "EXIT BY RULE"]


def system(*, day: str, killswitch_level: str, run_status: str | None) -> list[dict]:
    out = []
    if killswitch_level not in ("OK", None):
        out.append({"key": f"kill:{day}", "title": "Paper loop halted", "body": f"Kill switch: {killswitch_level}. A human reset is needed."})
    if run_status not in ("complete", "holiday", "not_a_session", None):
        out.append({"key": f"run:{day}", "title": "Today's live paper run did not complete",
                    "body": f"Status: {run_status}. Rebuild the day with fb paper-intraday --replay {day} (labelled replay)."})
    return out
