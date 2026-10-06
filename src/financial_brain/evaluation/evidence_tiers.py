"""Four kinds of evidence, never mixed: historical, walk-forward, paper-forward, live-forward.

A backtest and a live record do not carry the same weight, so they are never shown as one number. For each strategy:

    HISTORICAL      in-sample results over the archive (what the idea looked like where it was found)
    WALK-FORWARD    out-of-sample results: held-out years, frozen parameters (from the result notes)
    PAPER-FORWARD   the paper record made before outcomes existed (live paper days; replays are labelled apart)
    LIVE-FORWARD    real-money trades - none: this system places no orders

Confidence is taken from the forward tiers only: NONE without a passed walk-forward test, LIMITED until the paper
record reaches its verdict sample, and never higher than MODERATE while live-forward is empty.
"""
from __future__ import annotations

HISTORICAL, WALK, PAPER, LIVE = "historical", "walk-forward", "paper-forward", "live-forward"

#: From the result notes (brain/07-Research), per strategy: (historical, walk-forward), each (result, passed, source).
STATIC = {
    "naive momentum (champion)": (("+1.37%/mo net top 20, 2016-21 (t 2.1)", True, "Result of ranking v2"),
                                  ("-0.31%/mo top 20, 2022-26 (t -0.6); +0.48% on the 500 most liquid", False, "Result of ranking v2; deep library")),
    "ranking v1 (terminal list)": (("composite IC +0.083 (t 5.5) in the engine replay", True, "Result of the opportunity engine replay"),
                                   ("-0.34%/mo top 20, 2022-26; top-5 book -3.1%/trade vs universe", False, "Result of ranking v2; suggestion books")),
    "ranking v2 (challenger)": (("+1.65%/mo net top 20, 2016-21 (t 2.6)", True, "Result of ranking v2"),
                                ("+0.01% / +0.19%/mo, 2022-26 (t 0.0 / 0.5)", False, "Result of ranking v2")),
    "learned model (challenger)": (("in-sample fit not used", False, "Result of the deep feature library"),
                                   ("+0.77%/mo top 20, 2022-26 (t 2.3); vs momentum +0.29% (t 0.5)", True, "Result of the deep feature library")),
    "swing book (top 50)": (("-0.37%/trade vs universe 2023-26 (t -4.0), in-sample model", False, "Result of the suggestion paper books"),
                            ("swing setups lose 0.2-0.6% a trade net", False, "Result of swing setups")),
    "ORB intraday": (("gross ~0, net -15 to -21 bp over 251k trades", False, "Result of the opening-range breakout with stops"),
                     ("trend filters added nothing in the 2025-26 held-out year", False, "Result of trading with the market trend")),
}
PAPER_KEYS = {"ORB intraday": ("intraday", "ORB"), "swing book (top 50)": ("book", "swing-v1"),
              "ranking v1 (terminal list)": ("book", "book-v1")}
VERDICT_TRADES = 100


def ledger(*, intraday=None, book=None) -> list[dict]:
    out = []
    for name, (hist, walk) in STATIC.items():
        tiers = {HISTORICAL: {"result": hist[0], "passed": hist[1], "source": hist[2]},
                 WALK: {"result": walk[0], "passed": walk[1], "source": walk[2]},
                 PAPER: {"result": "not paper-traded yet", "passed": None, "n": 0},
                 LIVE: {"result": "none - no orders are placed by this system", "passed": None, "n": 0}}
        kind = PAPER_KEYS.get(name)
        try:
            if kind and kind[0] == "intraday" and intraday is not None:
                n, net, days = intraday.execute("""SELECT COUNT(*), SUM(net_pnl), COUNT(DISTINCT day) FROM trades
                                                   WHERE strategy = ? AND mode = 'live' AND exit_ts IS NOT NULL""", [kind[1]]).fetchone()
                tiers[PAPER] = {"result": f"{n} live paper trades over {days} day(s), net Rs {net or 0:,.0f}", "passed": None, "n": n}
            if kind and kind[0] == "book" and book is not None:
                n, = book.execute("SELECT COUNT(*) FROM positions WHERE version = ? AND mode = 'live' AND exit_day IS NOT NULL",
                                  [kind[1]]).fetchone()
                o, = book.execute("SELECT COUNT(*) FROM positions WHERE version = ? AND mode = 'live' AND exit_day IS NULL",
                                  [kind[1]]).fetchone()
                tiers[PAPER] = {"result": f"{n} sold, {o} open in the live paper book", "passed": None, "n": n}
        except Exception:                               # noqa: BLE001 - a missing table leaves the tier empty
            pass
        out.append({"strategy": name, "tiers": tiers, "confidence": confidence(tiers)})
    return out


def confidence(tiers: dict) -> str:
    if not tiers[WALK]["passed"]:
        return "NONE"
    if tiers[PAPER].get("n", 0) < VERDICT_TRADES:
        return "LIMITED"
    return "MODERATE"                                   # the ceiling while live-forward is empty
