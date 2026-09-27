"""The Decision Compiler: agents propose, arithmetic decides, and the rule that fired is named.

Without this layer a system has agents that *produce decisions*, and the decision is then
whatever the most fluent agent said. The compiler inverts that. Agents and engines contribute
**typed claims**; the compiler contributes no opinion at all. It assembles the claims, runs the
arithmetic that already exists (``expected_value``), consults the gates that already exist
(``safety``, ``portfolio/gate``), and resolves an action by a **fixed precedence** written here
in code.

    claims + scenarios + prices + gate + safety
                      |
              Decision Compiler   <- deterministic, Tier 0, no model call
                      |
        action, size, every number, every contradiction, and the rule that decided

## What it refuses to do

**It will not invent conviction.** If two analysts disagree, that is recorded as disagreement
and it *lowers* the outcome. Averaging a bull and a bear into a confident number is the single
most common way an ensemble launders uncertainty into a position.

**It will not fill in missing inputs.** No scenarios means no expected value, and no expected
value means ``ABSTAIN`` - not ``BUY`` at a default size. A decision with a hole in it is not a
cautious decision, it is an unmeasured one.

**It will not treat "I cannot tell" as "no".** ``AVOID`` means the arithmetic was done and came
out against. ``ABSTAIN`` means the arithmetic could not be done. Collapsing them destroys the
only signal that says which data to go and get, and it is why the gate distinguishes them too.

## The precedence, in order, because the order is the design

1. ``safety`` breaches - stale data, crisis regime, liquidity, group, drawdown. Deterministic
   and fatal. Checked first because it is free and because no edge justifies acting blind.
2. Missing or malformed inputs -> ``ABSTAIN``, naming what is missing.
3. Scenario arithmetic fails its own sum -> ``ABSTAIN`` (the input is broken, not the idea).
4. Expected value not positive -> ``AVOID``.
5. Portfolio gate ``ABSTAIN`` -> ``ABSTAIN``.
6. Portfolio gate ``REFUSE`` -> ``AVOID``, with the book's reason.
7. Edge positive but below ``MIN_EDGE`` after costs -> ``WATCH``. Real and too small is a
   distinct finding from wrong, and this project has produced it eighty-five times.
8. Claim disagreement above ``MAX_DISAGREEMENT`` -> ``WATCH``.
9. Otherwise ``BUY``, at the gate's approved weight.

Every outcome carries ``decided_by``: the name of the rule above that fired. A decision whose
provenance is "rule 7: edge below minimum" is auditable in a way that "confidence 0.82" is not.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from statistics import mean

from . import expected_value as ev
from . import safety

#: Net edge, per the decision's horizon, below which the answer is WATCH rather than BUY.
#: Eighty-five of this project's rejections were real edges too small to pay their costs, so
#: "positive but tiny" needs its own outcome rather than rounding up to a trade.
MIN_EDGE = 0.01
#: Dispersion of claim directions above which the compiler will not manufacture a view. 1.0 is
#: total disagreement (half the claims each way), 0.0 is unanimity.
MAX_DISAGREEMENT = 0.60
#: A claim's strength must be in [0, 1]; anything else is a typo or a scale mismatch, and
#: silently clamping it would hide the bug.
STRENGTH_RANGE = (0.0, 1.0)

BUY, WATCH, AVOID, ABSTAIN = "BUY", "WATCH", "AVOID", "ABSTAIN"


class CompileError(ValueError):
    """An input the compiler will not guess at."""


@dataclass
class Claim:
    """One typed contribution from one source. Prose goes in ``rationale`` and is never read
    by the compiler - it is carried for the human reading the record."""
    source: str
    direction: int                      # +1 bullish, -1 bearish, 0 explicitly neutral
    strength: float                     # 0..1, the source's own confidence in its claim
    evidence: list[str] = field(default_factory=list)
    rationale: str = ""
    horizon_days: int | None = None

    def validate(self) -> None:
        if self.direction not in (-1, 0, 1):
            raise CompileError(f"{self.source}: direction must be -1, 0 or +1")
        lo, hi = STRENGTH_RANGE
        if not lo <= self.strength <= hi:
            raise CompileError(
                f"{self.source}: strength {self.strength} outside [{lo}, {hi}] - a scale "
                f"mismatch clamped silently would be indistinguishable from a real view")


@dataclass
class Compiled:
    action: str
    decided_by: str
    weight: float
    reasons: list[str] = field(default_factory=list)
    numbers: dict = field(default_factory=dict)
    contradictions: list[str] = field(default_factory=list)
    inputs: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"action": self.action, "decided_by": self.decided_by,
                "weight": self.weight, "reasons": self.reasons,
                "numbers": self.numbers, "contradictions": self.contradictions,
                "inputs": self.inputs}


def aggregate(claims: list[Claim]) -> dict:
    """Net direction and, more importantly, how much the sources disagree.

    ``disagreement`` is the share of *signed strength* that cancels: with claims of +0.8 and
    −0.8 it is 1.0, and with +0.8 and +0.6 it is 0.0. It is not variance, because variance of
    two agreeing-but-differently-confident claims is non-zero and that is not disagreement.

    Neutral claims (direction 0) count in the denominator. An analyst who looked and had no view
    is evidence about the opportunity, not an absence of evidence.
    """
    for c in claims:
        c.validate()
    if not claims:
        return {"n": 0, "net": 0.0, "disagreement": 1.0, "by_source": {}}
    signed = [c.direction * c.strength for c in claims]
    gross = sum(abs(s) for s in signed)
    net = sum(signed)
    return {
        "n": len(claims),
        "net": net / len(claims),
        "gross_strength": gross / len(claims),
        # 1 - |net|/gross: the fraction of conviction that cancelled out.
        "disagreement": (1 - abs(net) / gross) if gross > 0 else 1.0,
        "bulls": sum(1 for c in claims if c.direction > 0),
        "bears": sum(1 for c in claims if c.direction < 0),
        "neutrals": sum(1 for c in claims if c.direction == 0),
        "mean_strength": mean(c.strength for c in claims),
        "by_source": {c.source: {"direction": c.direction, "strength": c.strength,
                                 "evidence": len(c.evidence)} for c in claims},
    }


def compile_decision(con, *, isin: str, as_of: date, claims: list[Claim],
                     scenarios: dict | None, entry: float | None,
                     invalidation: float | None, capital_inr: float,
                     gate_decision=None, horizon_days: int = 90,
                     min_edge: float = MIN_EDGE) -> Compiled:
    """Resolve typed inputs into one action, by the precedence in the module docstring.

    ``gate_decision`` is ``portfolio/gate.evaluate`` output. It is a parameter rather than
    computed here so the compiler stays free of the covariance estimation - a compiler that
    silently runs a 250-session query is one nobody can unit test.
    """
    out = Compiled(action=ABSTAIN, decided_by="", weight=0.0)
    agg = aggregate(claims)
    out.inputs = {"claims": agg, "isin": isin, "as_of": str(as_of),
                  "horizon_days": horizon_days}

    if agg["bulls"] and agg["bears"]:
        out.contradictions.append(
            f"{agg['bulls']} sources bullish and {agg['bears']} bearish on the same name "
            f"(disagreement {agg['disagreement']:.2f})")

    # --------------------------------------------------------------- 1. safety, fatal
    notional = capital_inr * 0.05
    sa = safety.assess(con, isin=isin, as_of=as_of, position_inr=notional)
    if not sa.safe:
        out.action, out.decided_by = AVOID, "1.safety"
        out.reasons = [f"safety.{b.check}: {b.detail}" for b in sa.breaches]
        return out

    # -------------------------------------------------------- 2. inputs present at all
    missing = []
    if not claims:
        missing.append("no claims from any source")
    if not scenarios:
        missing.append("no scenarios, so no expected value")
    if entry is None or entry <= 0:
        missing.append("no entry price")
    if invalidation is None or invalidation <= 0:
        missing.append("no invalidation level, so no size can be derived")
    if missing:
        out.action, out.decided_by = ABSTAIN, "2.missing_inputs"
        out.reasons = missing
        return out

    # ------------------------------------------------- 3. does the arithmetic hold up
    try:
        assessment = ev.assess(scenarios)
        sizing = ev.size(portfolio_inr=capital_inr, entry=entry,
                         invalidation=invalidation)
    except ev.EVError as exc:
        out.action, out.decided_by = ABSTAIN, "3.broken_arithmetic"
        out.reasons = [f"scenarios or sizing will not compute: {exc}"]
        return out

    out.numbers = {
        "expected_value": assessment.expected_value,
        "downside": getattr(assessment, "downside", None),
        "worst_case": getattr(assessment, "worst_case", None),
        "entry": entry, "invalidation": invalidation,
        "stop_distance": sizing["stop_distance"],
        "sized_weight": sizing["weight"],
        "rupees_at_risk": sizing["rupees_at_risk"],
        "disagreement": agg["disagreement"],
        "net_direction": agg["net"],
    }

    # ------------------------------------------------------- 4. is it worth taking at all
    if not assessment.positive():
        out.action, out.decided_by = AVOID, "4.negative_expected_value"
        out.reasons = [f"expected value {assessment.expected_value:+.2%}: "
                       f"{assessment.describe()}"]
        return out

    # ------------------------------------------------------------- 5/6. the portfolio gate
    weight = sizing["weight"]
    if gate_decision is not None:
        gv = getattr(gate_decision, "verdict", None) or gate_decision.get("verdict")
        greasons = (getattr(gate_decision, "reasons", None)
                    or gate_decision.get("reasons") or [])
        gweight = (getattr(gate_decision, "approved_weight", None)
                   if not isinstance(gate_decision, dict)
                   else gate_decision.get("approved_weight"))
        out.numbers["gate_verdict"] = gv
        out.numbers["gate_approved_weight"] = gweight
        if gv == "ABSTAIN":
            out.action, out.decided_by = ABSTAIN, "5.gate_abstain"
            out.reasons = list(greasons)
            return out
        if gv == "REFUSE":
            out.action, out.decided_by = AVOID, "6.gate_refuse"
            out.reasons = list(greasons)
            return out
        if gv == "RESIZE" and gweight:
            weight = min(weight, gweight)
            out.reasons.append(
                f"portfolio gate resized {sizing['weight']:.2%} -> {weight:.2%}")
        elif gweight:
            weight = min(weight, gweight)

    # --------------------------------------------------------- 7. real but too small
    if assessment.expected_value < min_edge:
        out.action, out.decided_by = WATCH, "7.edge_below_minimum"
        out.weight = 0.0
        out.reasons.append(
            f"expected value {assessment.expected_value:+.2%} is positive but below the "
            f"{min_edge:.1%} minimum for this horizon - real and too small is a distinct "
            f"finding from wrong, and it is the one this project keeps producing")
        return out

    # ---------------------------------------------------------- 8. sources disagree
    if agg["disagreement"] > MAX_DISAGREEMENT:
        out.action, out.decided_by = WATCH, "8.sources_disagree"
        out.weight = 0.0
        out.reasons.append(
            f"disagreement {agg['disagreement']:.2f} above {MAX_DISAGREEMENT:.2f}: "
            f"{agg['bulls']} bullish, {agg['bears']} bearish. Averaging these into a "
            f"position would launder the disagreement rather than resolve it")
        return out

    # ------------------------------------------------------------------- 9. take it
    out.action, out.decided_by, out.weight = BUY, "9.passed_every_gate", weight
    out.reasons.append(
        f"expected value {assessment.expected_value:+.2%}, sized {weight:.2%} from a "
        f"{sizing['stop_distance']:.1%} stop distance, {agg['bulls']}/{agg['n']} sources "
        f"bullish at disagreement {agg['disagreement']:.2f}")
    return out
