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
#: Historical net Sharpe above 6.5% (monthly, annualised) for 2022-26, its probabilistic Sharpe and its deflated Sharpe
#: against 330 trials (Result of Sharpe ratio and Sharpe allocation). None where no monthly series was tested.
SHARPE = {"naive momentum (champion)": (0.22, 0.68, 0.03), "ranking v1 (terminal list)": (0.33, 0.75, 0.05),
          "ranking v2 (challenger)": (0.44, 0.82, 0.07), "learned model (challenger)": (0.88, 0.97, 0.29),
          "swing book (top 50)": None, "ORB intraday": (-8.99, 0.00, 0.00)}
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
        sh = SHARPE.get(name)
        out.append({"strategy": name, "tiers": tiers, "confidence": confidence(tiers),
                    "sharpe": dict(zip(("sharpe", "psr", "dsr"), sh)) if sh else None})
    return out


def confidence(tiers: dict) -> str:
    if not tiers[WALK]["passed"]:
        return "NONE"
    if tiers[PAPER].get("n", 0) < VERDICT_TRADES:
        return "LIMITED"
    return "MODERATE"                                   # the ceiling while live-forward is empty


# ------------------------------------------------------------------ the claim a displayed opportunity may make

OBSERVATION, REPLICATED, FORWARD_PAPER, CONFIRMED = ("Historical observation", "Replicated candidate",
                                                     "Forward-paper evidence", "Confirmed strategy")
CLAIMS = {
    OBSERVATION: "an interesting pattern only - it has not passed the historical gates",
    REPLICATED: "passed the internal historical gates (ADR-0004 G1-G4); paper only",
    FORWARD_PAPER: "predicted before the outcome and tracked on paper since; still zero real capital",
    CONFIRMED: "passed the full out-of-sample and paper criteria (ADR-0004 G5)",
}
#: The strategy behind each list the terminal shows.
LIST_STRATEGY = {"terminal list": "ranking v1 (terminal list)", "ranking v2": "ranking v2 (challenger)",
                 "learned model": "learned model (challenger)"}


def claim_tier(*, replicated: bool, paper_forward: bool, confirmed: bool) -> str:
    """The one claim allowed. Monotone: each tier needs every tier below it, so a paper record on a strategy that
    failed its historical gates is still an observation, and nothing is 'confirmed' without G5."""
    if not replicated:
        return OBSERVATION
    if confirmed and paper_forward:
        return CONFIRMED
    return FORWARD_PAPER if paper_forward else REPLICATED


def list_claim(strategy: str, *, paper_forward: bool = False, confirmed: bool = False) -> str:
    """The claim for a list produced by one of the ``STATIC`` strategies: replicated means its walk-forward passed."""
    walk = STATIC.get(strategy)
    return claim_tier(replicated=bool(walk and walk[1][1]), paper_forward=paper_forward, confirmed=confirmed)
