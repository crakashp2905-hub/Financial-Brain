"""C05 - Indian transaction cost model.

A backtest without these numbers is fiction. Indian round-trip friction is a large
multiple of a US equivalent, and it is *asymmetric*: STT on delivery is charged on both
legs, which is what kills high-turnover factor strategies that look excellent on paper.

Rates below are defaults as configured, not constants of nature - they are revised in
budgets and circulars. Override them via ``CostModel(...)`` and record which schedule a
backtest used. Impact cost is an estimate keyed to the liquidity bucket from
``universe.snapshot.liquidity_buckets``; it is the single largest and least certain term
outside the top few hundred names.

Everything is expressed as a fraction of turnover unless named ``_flat``.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum


class Segment(str, Enum):
    DELIVERY = "delivery"     # equity delivery (CNC)
    INTRADAY = "intraday"     # equity intraday (MIS)
    FUT = "futures"
    OPT = "options"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


# Impact cost by liquidity bucket, one-way, as a fraction of notional.
# Deliberately pessimistic outside the mega/large buckets - see FEASIBILITY-INDIA.md.
DEFAULT_IMPACT = {
    "mega": 0.0005,    # top 100 by turnover
    "large": 0.0010,   # 101-300
    "mid": 0.0025,     # 301-750
    "small": 0.0060,   # 751-1500
    "micro": 0.0150,   # beyond
}


@dataclass(frozen=True)
class CostModel:
    """Indian cash and F&O cost schedule."""

    # Securities Transaction Tax
    stt_delivery: float = 0.001       # both legs
    stt_intraday_sell: float = 0.00025
    stt_futures_sell: float = 0.0002
    stt_options_sell: float = 0.001   # on premium

    # Exchange transaction charges
    exch_txn_equity: float = 0.0000297
    exch_txn_futures: float = 0.0000173
    exch_txn_options: float = 0.0003503   # on premium

    # Statutory
    sebi_turnover: float = 0.000001
    stamp_buy_delivery: float = 0.000015
    stamp_buy_intraday: float = 0.000003
    gst_rate: float = 0.18                # on brokerage + exchange + SEBI charges

    # Broker
    brokerage_rate: float = 0.0003        # discount brokers: 0 on delivery, 0.03% intraday
    brokerage_cap_flat: float = 20.0
    brokerage_delivery_flat: float = 0.0

    impact: dict = field(default_factory=lambda: dict(DEFAULT_IMPACT))

    # ------------------------------------------------------------------ core
    def one_way(self, *, turnover: float, side: Side, segment: Segment = Segment.DELIVERY,
                bucket: str = "large", include_impact: bool = True) -> dict:
        """Cost of a single leg. Returns every component, not just a total.

        Components are itemised deliberately: when a strategy dies on costs you need to
        know *which* cost killed it, because the remedies differ (lower turnover vs
        larger caps vs different segment).
        """
        turnover = abs(float(turnover))
        sell = side == Side.SELL

        # STT
        if segment == Segment.DELIVERY:
            stt = turnover * self.stt_delivery
        elif segment == Segment.INTRADAY:
            stt = turnover * self.stt_intraday_sell if sell else 0.0
        elif segment == Segment.FUT:
            stt = turnover * self.stt_futures_sell if sell else 0.0
        else:
            stt = turnover * self.stt_options_sell if sell else 0.0

        # Exchange
        if segment in (Segment.DELIVERY, Segment.INTRADAY):
            exch = turnover * self.exch_txn_equity
        elif segment == Segment.FUT:
            exch = turnover * self.exch_txn_futures
        else:
            exch = turnover * self.exch_txn_options

        sebi = turnover * self.sebi_turnover

        # Stamp duty - buy side only
        if sell:
            stamp = 0.0
        elif segment == Segment.DELIVERY:
            stamp = turnover * self.stamp_buy_delivery
        else:
            stamp = turnover * self.stamp_buy_intraday

        # Brokerage
        if segment == Segment.DELIVERY:
            brokerage = self.brokerage_delivery_flat
        else:
            brokerage = min(turnover * self.brokerage_rate, self.brokerage_cap_flat)

        gst = (brokerage + exch + sebi) * self.gst_rate
        impact = turnover * self.impact.get(bucket, self.impact["mid"]) if include_impact else 0.0

        total = stt + exch + sebi + stamp + brokerage + gst + impact
        return {
            "turnover": turnover, "side": side.value, "segment": segment.value,
            "bucket": bucket, "stt": stt, "exchange": exch, "sebi": sebi, "stamp": stamp,
            "brokerage": brokerage, "gst": gst, "impact": impact,
            "total": total, "bps": (total / turnover * 10_000) if turnover else 0.0,
        }

    def round_trip(self, *, turnover: float, segment: Segment = Segment.DELIVERY,
                   bucket: str = "large", include_impact: bool = True) -> dict:
        """Buy then sell the same notional. This is the number that kills strategies."""
        buy = self.one_way(turnover=turnover, side=Side.BUY, segment=segment,
                           bucket=bucket, include_impact=include_impact)
        sell = self.one_way(turnover=turnover, side=Side.SELL, segment=segment,
                            bucket=bucket, include_impact=include_impact)
        total = buy["total"] + sell["total"]
        return {
            "buy": buy, "sell": sell, "total": total,
            "bps": (total / turnover * 10_000) if turnover else 0.0,
        }

    # ------------------------------------------------------- strategy screens
    def breakeven_move(self, *, segment: Segment = Segment.DELIVERY,
                       bucket: str = "large") -> float:
        """Fractional price move needed just to cover one round trip."""
        return self.round_trip(turnover=1_000_000, segment=segment, bucket=bucket)["bps"] / 10_000

    def annual_drag(self, *, turnover_per_year: float,
                    segment: Segment = Segment.DELIVERY, bucket: str = "large") -> float:
        """Annual return drag for a strategy turning the portfolio over N times a year.

        ``turnover_per_year`` of 12 means the whole book is replaced monthly. Feeding this
        into the India Implementability Gate (C19) is how a hypothesis gets rejected
        *before* anyone wastes a backtest on it.
        """
        return turnover_per_year * self.round_trip(
            turnover=1_000_000, segment=segment, bucket=bucket)["bps"] / 10_000

    def as_dict(self) -> dict:
        """Serialise the schedule so a backtest can record which costs it assumed."""
        return asdict(self)


DEFAULT = CostModel()
