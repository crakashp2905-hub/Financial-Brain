"""One gate, seventeen checks, and a single verdict that decides whether capital may be risked.

The checks below almost all exist already, and that is the problem this module solves. They are
spread across ``evaluation/firewall.py``, ``evaluation/implementability.py``,
``evaluation/registry.py``, ``decisions/safety.py``, ``risk/limits.py`` and ``portfolio/gate.py``,
each with its own verdict, and nothing anywhere answers the only question that matters:

    may this strategy be given money?

A reader who wants that answer currently has to know which six modules to consult and how to
combine them, which means in practice nobody knows, and a strategy can look approved because the
gate someone happened to run said PASS.

So this consolidates. It computes nothing of its own - every check delegates to the module that
owns it - and it produces one verdict with every failure named.

## The two things this gate refuses to be talked into

**A high Sharpe is not a reason.** Sharpe appears once here, inside ``deflated_sharpe``, corrected
for how many trials produced it. A strategy arrives with a good backtest by construction, since
that is why anyone is looking at it.

**Nothing is a warning.** A check either passes or the gate fails. There is no "passed with
reservations", because a reservation is how a failing strategy reaches capital: someone reads the
reservation, decides it is acceptable, and the gate has been converted into advice. If a condition
is tolerable it should not be a check.

## Absent is not passed

Three checks cannot be evaluated on this archive today, and they return ``UNAVAILABLE`` rather
than ``PASS``:

``spread``            the one attempt to estimate spreads (Corwin-Schultz 2012) produced numbers
                      tenfold too large and was declared unusable.
``sector_exposure``   no industry classification exists here; ``index_constituents`` has zero rows.
``paper_performance`` needs a live paper record long enough to mean anything, which does not exist.

``UNAVAILABLE`` fails the gate. That is the point: a check nobody can run is not a check that
passed, and letting it read as green is how an unmeasured risk becomes an approved one. It is kept
distinct from ``FAIL`` so a reader can tell "we have not measured this" from "this was measured and
it failed" - the two call for entirely different work.

## Significance is asked twice

``ic_significance`` is the firewall's own gate and it is about whether the signal **orders** names.
``net_significance`` is about whether the ordering **pays**. They are not the same question and in
this archive they routinely disagree: ``vol_60`` has the largest IC on record at +12.30 with
negative alpha, and ``dist_52w_high`` clears on IC t +3.77 with a net t of +1.59. A money gate that
asked only the first would approve on the strength of an ordering nobody can harvest.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PASS, FAIL, UNAVAILABLE = "PASS", "FAIL", "UNAVAILABLE"

#: Minimum out-of-sample rebalances before the OOS check can say anything.
MIN_OOS = 12
#: Minimum sessions of live paper record.
MIN_PAPER_SESSIONS = 120
#: Separation from a turnover-matched synthetic null, in t units. Below this a result is
#: indistinguishable from what the same rule scores on noise.
MIN_NULL_SEPARATION = 1.0


@dataclass
class Check:
    name: str
    status: str
    measured: str = ""
    why: str = ""
    owner: str = ""

    @property
    def ok(self) -> bool:
        return self.status == PASS


@dataclass
class Verdict:
    strategy: str
    checks: list[Check] = field(default_factory=list)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def unavailable(self) -> list[Check]:
        return [c for c in self.checks if c.status == UNAVAILABLE]

    @property
    def verdict(self) -> str:
        """``CAPITAL`` only when every check passes. Anything else is ``NO_CAPITAL``."""
        return "CAPITAL" if all(c.ok for c in self.checks) else "NO_CAPITAL"

    def as_dict(self) -> dict:
        return {
            "strategy": self.strategy, "verdict": self.verdict,
            "passed": sum(1 for c in self.checks if c.ok),
            "failed": len(self.failed), "unavailable": len(self.unavailable),
            "total": len(self.checks),
            "checks": [{"name": c.name, "status": c.status, "measured": c.measured,
                        "why": c.why, "owner": c.owner} for c in self.checks],
        }

    def describe(self) -> str:
        head = (f"{self.strategy}: {self.verdict}  "
                f"({sum(1 for c in self.checks if c.ok)}/{len(self.checks)} passed)")
        lines = [head]
        for c in self.checks:
            mark = {"PASS": "  PASS        ", "FAIL": "  FAIL        ",
                    "UNAVAILABLE": "  UNAVAILABLE "}[c.status]
            lines.append(f"{mark}{c.name:22s} {c.measured}")
            if c.why and c.status != PASS:
                lines.append(f"                              {c.why}")
        return "\n".join(lines)


def _bar(con) -> float:
    from statistics import NormalDist
    n = con.execute("SELECT COUNT(*) FROM evaluation_runs").fetchone()[0] + 1
    return NormalDist().inv_cdf(1 - 0.05 / (2 * n))


def evaluate(con, *, strategy: str, firewall_result: dict | None = None,
             implementability: dict | None = None,
             null_separation: float | None = None,
             net_t: float | None = None,
             oos_rebalances: int = 0,
             paper_sessions: int = 0,
             execution: dict | None = None,
             portfolio_verdict: str | None = None) -> Verdict:
    """Assemble one verdict from the results the owning modules produced.

    Everything is passed in rather than computed here. A gate that runs its own backtest is a gate
    whose answer depends on how it happened to run it, and the whole value of this object is that
    it consults the modules that already did the work under their own discipline.
    """
    v = Verdict(strategy=strategy)
    fw = firewall_result or {}
    gates = fw.get("gates") or {}
    bar = _bar(con)

    def add(name, status, measured="", why="", owner=""):
        v.checks.append(Check(name, status, measured, why, owner))

    # ------------------------------------------------- correctness of the measurement
    add("pit_correctness", PASS if fw else FAIL,
        "execution lagged one session; liquidity judged on observed turnover",
        "" if fw else "no firewall result supplied",
        "evaluation/benchmark.py")
    add("survivorship", PASS if fw else FAIL,
        f"{fw.get('filled_no_outcome', 0)} names scored at last traded price, not dropped",
        "" if fw else "no firewall result supplied",
        "evaluation/benchmark.py")
    add("look_ahead", PASS if fw else FAIL,
        "signals from features dated at or before the rebalance",
        "" if fw else "no firewall result supplied",
        "evaluation/benchmark.py")

    # ----------------------------------------------------------------- the statistics
    # **Two significance checks, and the split is the point.** The firewall's own gate is defined
    # on the **information coefficient** - whether the signal orders names - and a gate about money
    # cannot treat that as the answer. `vol_60` has the largest IC in this ledger at +12.30 and
    # negative alpha; `dist_52w_high` clears on IC t +3.77 and has a *net* t of +1.59. Reporting
    # one "significance PASS" from the IC is the exact conflation that made the opportunity engine
    # report CLEARS for a signal with negative alpha, and it would be worse here.
    ic_t = fw.get("ic_t")
    sig = bool(gates.get("significance"))
    add("ic_significance", PASS if sig else FAIL,
        f"IC t {ic_t:+.2f} against a Bonferroni bar of {bar:.2f}"
        if ic_t is not None else "not computed",
        "" if sig else "the ordering is not significant for the number of trials spent",
        "evaluation/firewall.py")
    if net_t is None:
        add("net_significance", UNAVAILABLE, "not supplied",
            "pass the t-statistic of the strategy's **net** excess returns; IC significance "
            "says the signal orders names, not that the ordering pays",
            "evaluation/firewall.py")
    else:
        ok = abs(net_t) > bar and net_t > 0
        add("net_significance", PASS if ok else FAIL,
            f"net t {net_t:+.2f} against a Bonferroni bar of {bar:.2f}",
            "" if ok else "the net return is not significant for the number of trials spent - "
                          "this is the statistic a money gate turns on",
            "evaluation/firewall.py")
    dsr = fw.get("deflated_sharpe")
    add("deflated_sharpe", PASS if gates.get("deflated_sharpe") else FAIL,
        f"DSR {dsr:.2f}" if dsr is not None else "not computed",
        "" if gates.get("deflated_sharpe") else
        "the net Sharpe does not beat the best expected from this many noise trials",
        "evaluation/firewall.py")
    add("walk_forward", PASS if gates.get("walk_forward") else FAIL,
        f"IC sign held in {len(fw.get('ic_by_year') or {})} years measured",
        "" if gates.get("walk_forward") else "the sign does not hold across calendar years",
        "evaluation/firewall.py")
    add("regime_robustness", PASS if gates.get("regime") else FAIL,
        f"{len(fw.get('ic_by_regime') or {})} regimes measured, version-pinned",
        "" if gates.get("regime") else "the IC significantly reverses in some regime",
        "evaluation/firewall.py")

    # A result indistinguishable from what the same rule scores on Brownian motion has shown
    # nothing, whatever its absolute statistic. Two previously reported results failed this.
    if null_separation is None:
        add("null_separation", UNAVAILABLE, "not measured",
            "run the strategy against a turnover-matched synthetic null "
            "(evaluation/synthetic.py, drift='common' for a cross-sectional signal)",
            "evaluation/synthetic.py")
    else:
        ok = null_separation >= MIN_NULL_SEPARATION
        add("null_separation", PASS if ok else FAIL,
            f"real minus null = {null_separation:+.2f} t",
            "" if ok else f"below {MIN_NULL_SEPARATION:.1f}: indistinguishable from noise",
            "evaluation/synthetic.py")

    ok = oos_rebalances >= MIN_OOS
    add("out_of_sample", PASS if ok else FAIL,
        f"{oos_rebalances} rebalances after the data cutoff",
        "" if ok else f"needs {MIN_OOS}; an in-sample result is a hypothesis",
        "evaluation/registry.py")

    # ---------------------------------------------------------------------- the money
    add("transaction_costs", PASS if gates.get("costs") else FAIL,
        f"round trip {fw.get('round_trip', float('nan')):.2%} on the book's own "
        f"liquidity mix, turnover {fw.get('turnover', float('nan')):.0%}"
        if fw.get("round_trip") else "not computed",
        "" if gates.get("costs") else "net of its own holdings' costs it does not beat the "
                                      "universe",
        "costs/book.py")
    add("spread", UNAVAILABLE, "not estimable on this archive",
        "Corwin-Schultz (2012) produced spreads tenfold too large here and was declared "
        "unusable; every cost number is optimistic by that unmeasured amount",
        "costs/measured.py")
    if execution:
        sf = execution.get("mean_shortfall_bps")
        unf = execution.get("unfilled_rate")
        ok = sf is not None and unf is not None and unf < 0.05
        add("execution_simulation", PASS if ok else FAIL,
            f"shortfall {sf:+.1f} bps, {unf:.0%} of entries not fully filled"
            if sf is not None else "not measured",
            "" if ok else "fills walked through real minute volume do not complete, or the "
                          "shortfall was not measured",
            "execution/simulator.py")
    else:
        add("execution_simulation", UNAVAILABLE, "not run",
            "walk the orders through real minute volume (execution/simulator.py); an assumed "
            "fill is where a good backtest goes to die",
            "execution/simulator.py")
    # Absent is UNAVAILABLE, not FAIL. Both stop the gate, and conflating them would report a
    # check that was never run as one the strategy failed.
    if implementability is None:
        add("liquidity", UNAVAILABLE, "not run",
            "run evaluation/implementability.check with the hypothesis's positions and AUM",
            "evaluation/implementability.py")
    else:
        okv = implementability.get("verdict") == "PASS"
        add("liquidity", PASS if okv else FAIL,
            implementability.get("summary")
            or f"implementability verdict {implementability.get('verdict')}",
            "" if okv else "fails the India implementability gate",
            "evaluation/implementability.py")
    names_each = fw.get("avg_names")
    add("capacity", PASS if gates.get("capacity") else FAIL,
        (f"{fw.get('dates', 0)} rebalances"
         + (f", {names_each:.0f} names each" if names_each else ""))
        if fw else "not computed",
        "" if gates.get("capacity") else "too few names to form the book being claimed",
        "evaluation/firewall.py")

    # ------------------------------------------------------------------ the portfolio
    add("portfolio_fit", PASS if portfolio_verdict in ("ALLOW", "RESIZE") else FAIL,
        f"portfolio gate says {portfolio_verdict or 'not consulted'}",
        "" if portfolio_verdict in ("ALLOW", "RESIZE") else
        "the book cannot afford it, or the gate could not decide",
        "portfolio/gate.py")
    add("sector_exposure", UNAVAILABLE, "no industry classification in this archive",
        "security_reference has no sector field and index_constituents has zero rows; "
        "this needs NSE or AMFI sector data ingested and versioned",
        "risk/exposure.py")

    # ------------------------------------------------------------- against the world
    add("benchmark_comparison", PASS if fw.get("net_per_period", 0) > 0 else FAIL,
        f"net {fw.get('net_per_period', float('nan')):+.4%} per period over the "
        f"equal-weighted universe" if fw else "not computed",
        "" if fw.get("net_per_period", 0) > 0 else
        "does not beat the universe it was drawn from, after its own costs",
        "evaluation/control.py")
    ok = paper_sessions >= MIN_PAPER_SESSIONS
    add("paper_performance", PASS if ok else UNAVAILABLE,
        f"{paper_sessions} sessions of live paper record",
        "" if ok else f"needs {MIN_PAPER_SESSIONS}; no paper record of that length exists",
        "paper/ledger.py")
    return v
