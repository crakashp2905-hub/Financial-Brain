"""ETF reference data, separate return series and premium/discount anomalies (checklist sections 2, 5, 20).

* ``EtfRef`` holds effective-dated reference facts (exposure class, benchmark preferring a TRI, fund
  house, listing dates, TER, tracking difference and error) with a source per version.
* ``load_reference`` reads permitted disclosure rows the owner has downloaded; it fetches nothing.
* Price-return, NAV-return and benchmark total-return series are distinct labelled types.
* ``premium_anomaly`` flags a premium/discount only from synchronous, fresh iNAV inside a trading
  window, and labels it an observation: there is no instant retail redemption pathway.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum

from financial_brain.instruments.pit import Inav, Quote, inav_premium


class EtfRefError(ValueError):
    pass


class Exposure(str, Enum):
    EQUITY_BROAD = "EQUITY_BROAD"
    EQUITY_SECTOR = "EQUITY_SECTOR"
    EQUITY_FACTOR = "EQUITY_FACTOR"
    DEBT = "DEBT"
    GOLD = "GOLD"
    SILVER = "SILVER"
    INTERNATIONAL = "INTERNATIONAL"
    OTHER = "OTHER"


@dataclass(frozen=True)
class EtfVersion:
    effective_from: date
    source: str
    exposure: Exposure
    benchmark: str
    benchmark_is_tri: bool
    fund_house: str
    listed_on: date
    ter: Decimal | None = None
    tracking_difference: Decimal | None = None
    tracking_error: Decimal | None = None
    status: str = "LISTED"


@dataclass
class EtfRef:
    uid: str
    symbol: str
    versions: list[EtfVersion] = field(default_factory=list)
    #: Creation/redemption is issuer and market-maker context only (FB-0211).
    retail_redemption_path: bool = False

    def add(self, v: EtfVersion) -> None:
        if any(x.effective_from == v.effective_from for x in self.versions):
            raise EtfRefError("duplicate version date")
        self.versions.append(v)
        self.versions.sort(key=lambda x: x.effective_from)

    def as_of(self, on: date) -> EtfVersion | None:
        vs = [v for v in self.versions if v.effective_from <= on]
        return vs[-1] if vs else None


_EXPOSURE_WORDS = [("gold", Exposure.GOLD), ("silver", Exposure.SILVER), ("gilt", Exposure.DEBT), ("g-sec", Exposure.DEBT),
                   ("bond", Exposure.DEBT), ("liquid", Exposure.DEBT), ("nasdaq", Exposure.INTERNATIONAL),
                   ("s&p 500", Exposure.INTERNATIONAL), ("hang seng", Exposure.INTERNATIONAL),
                   ("bank", Exposure.EQUITY_SECTOR), ("it", Exposure.EQUITY_SECTOR),
                   ("pharma", Exposure.EQUITY_SECTOR), ("momentum", Exposure.EQUITY_FACTOR),
                   ("low vol", Exposure.EQUITY_FACTOR), ("value", Exposure.EQUITY_FACTOR),
                   ("quality", Exposure.EQUITY_FACTOR), ("nifty 50", Exposure.EQUITY_BROAD),
                   ("sensex", Exposure.EQUITY_BROAD), ("nifty 100", Exposure.EQUITY_BROAD)]


def classify(benchmark: str) -> Exposure:
    """Exposure class from the benchmark name; unknown names stay OTHER, not guessed."""
    b = f" {benchmark.lower()} "
    for word, exp in _EXPOSURE_WORDS:
        if f" {word} " in b or b.strip().startswith(word):
            return exp
    return Exposure.OTHER


def load_reference(rows: list[dict], source: str) -> tuple[dict[str, EtfRef], list[tuple[dict, str]]]:
    """Build references from owner-provided disclosure rows; returns refs and refused rows."""
    refs: dict[str, EtfRef] = {}
    refused = []
    for r in rows:
        try:
            uid = f"IN:{r['isin']}"
            ref = refs.setdefault(uid, EtfRef(uid, r["symbol"]))
            bench = r["benchmark"]
            ref.add(EtfVersion(
                date.fromisoformat(r["effective_from"]), source, classify(bench), bench,
                bench.upper().endswith("TRI"), r["fund_house"], date.fromisoformat(r["listed_on"]),
                Decimal(r["ter"]) if r.get("ter") else None,
                Decimal(r["tracking_difference"]) if r.get("tracking_difference") else None,
                Decimal(r["tracking_error"]) if r.get("tracking_error") else None))
        except (KeyError, ValueError, EtfRefError) as exc:
            refused.append((r, str(exc)))
    return refs, refused


class SeriesKind(str, Enum):
    PRICE_RETURN = "PRICE_RETURN"
    NAV_RETURN = "NAV_RETURN"
    BENCHMARK_TR = "BENCHMARK_TR"


@dataclass(frozen=True)
class Series:
    kind: SeriesKind
    uid: str
    points: tuple[tuple[date, float], ...]

    def total(self) -> float:
        if len(self.points) < 2:
            raise EtfRefError("need two points")
        return self.points[-1][1] / self.points[0][1] - 1


def tracking(price: Series, benchmark: Series) -> float:
    """Tracking difference of the *market price* series against the benchmark total return."""
    if price.kind is not SeriesKind.PRICE_RETURN or benchmark.kind is not SeriesKind.BENCHMARK_TR:
        raise EtfRefError("tracking needs a price series and a benchmark total-return series")
    if [d for d, _ in price.points] != [d for d, _ in benchmark.points]:
        raise EtfRefError("series must share dates")
    return price.total() - benchmark.total()


@dataclass(frozen=True)
class Anomaly:
    premium: Decimal
    label: str = "OBSERVATION_NOT_ARBITRAGE"


def premium_anomaly(q: Quote, inav: Inav | None, *, threshold: Decimal, decision_at: datetime,
                    window: tuple[time, time], tz, max_skew: timedelta = timedelta(seconds=5),
                    max_inav_age: timedelta = timedelta(seconds=30),
                    underlying_open: bool = True) -> Anomaly | None:
    """Flag |premium| above threshold, only inside the window and with a usable iNAV (FB-0505)."""
    local = decision_at.astimezone(tz).time()
    if not (window[0] <= local <= window[1]) or not underlying_open:
        return None
    p = inav_premium(q, inav, max_skew=max_skew, max_inav_age=max_inav_age, decision_at=decision_at)
    if p.fraction is None or abs(p.fraction) < threshold:
        return None
    return Anomaly(p.fraction)


def executable_fair_gap(q: Quote, inav: Inav | None, side: str, *, decision_at: datetime,
                        max_skew: timedelta, max_inav_age: timedelta) -> Decimal | None:
    """Executable price minus fresh iNAV for the intended side; None when not comparable (FB-2004)."""
    p = inav_premium(q, inav, max_skew=max_skew, max_inav_age=max_inav_age, decision_at=decision_at)
    if p.fraction is None or inav is None:
        return None
    px = q.ask if side == "BUY" else q.bid
    return px / inav.value - 1
