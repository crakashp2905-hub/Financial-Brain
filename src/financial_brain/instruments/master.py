"""Canonical instrument identity, effective-dated and point-in-time (FB-0101, 0102, 0106, 0107, 0108, 0110).

Three questions are kept apart:

* **Which instrument is this?** A ``uid`` that is never reused. Symbols, ISINs and provider tokens are
  versions of it with an effective span, so the same ticker at two dates can resolve to two instruments.
* **What was true of it on a date?** Terms (tick, lot, expiry, an ETF's index, a scheme's plan) and state
  (listed, suspended, closed for purchase) are effective-dated.
* **When could we have known?** Every record carries when it was published. A lookup takes ``known_at`` and
  ignores anything published later, so a replay cannot see a future listing, lot size or suspension.

The master rejects unknown identities, overlapping active identities and records published in the future.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from financial_brain.kernel.clock import ClockError, utc
from financial_brain.kernel.units import to_decimal


class AssetClass(str, Enum):
    EQUITY = "EQUITY"
    ETF = "ETF"
    OPTION = "OPTION"
    MUTUAL_FUND = "MUTUAL_FUND"
    FUTURE = "FUTURE"
    INDEX = "INDEX"          # reference only: an index is an underlying, never an order


class Desk(str, Enum):
    SWING = "SWING"
    INTRADAY = "INTRADAY"
    OPTIONS = "OPTIONS"
    ETF = "ETF"
    MUTUAL_FUND = "MUTUAL_FUND"


#: A desk is a way of trading, not an asset class: an ETF can be a swing or an intraday trade.
DESK_ASSETS: dict[Desk, frozenset[AssetClass]] = {
    Desk.SWING: frozenset({AssetClass.EQUITY, AssetClass.ETF}),
    Desk.INTRADAY: frozenset({AssetClass.EQUITY, AssetClass.ETF}),
    Desk.OPTIONS: frozenset({AssetClass.OPTION}),
    Desk.ETF: frozenset({AssetClass.ETF}),
    Desk.MUTUAL_FUND: frozenset({AssetClass.MUTUAL_FUND}),
}


def desk_allows(desk: Desk, asset_class: AssetClass) -> bool:
    return asset_class in DESK_ASSETS[desk]


class InstrumentState(str, Enum):
    LISTED = "LISTED"
    SUSPENDED = "SUSPENDED"
    HALTED = "HALTED"
    DELISTED = "DELISTED"
    MATURED = "MATURED"
    CLOSED_FOR_PURCHASE = "CLOSED_FOR_PURCHASE"
    CLOSED_FOR_REDEMPTION = "CLOSED_FOR_REDEMPTION"
    RESTRICTED = "RESTRICTED"


_ENTRY_OK = frozenset({InstrumentState.LISTED, InstrumentState.CLOSED_FOR_REDEMPTION})
_EXIT_OK = frozenset({InstrumentState.LISTED, InstrumentState.CLOSED_FOR_PURCHASE})
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


class InstrumentError(ValueError):
    pass


@dataclass(frozen=True)
class Provenance:
    """Where a record came from and when it became public. ``published_at`` is what lookups filter on."""

    source: str
    source_hash: str
    published_at: datetime
    ingested_at: datetime

    def __post_init__(self) -> None:
        if not self.source:
            raise InstrumentError("a record needs a named source")
        try:
            object.__setattr__(self, "published_at", utc(self.published_at))
            object.__setattr__(self, "ingested_at", utc(self.ingested_at))
        except ClockError as exc:
            raise InstrumentError(str(exc)) from exc
        if self.published_at > self.ingested_at:
            raise InstrumentError("a record cannot be ingested before it was published")


@dataclass(frozen=True)
class InstrumentId:
    """One effective-dated version of an instrument's identity on one exchange."""

    uid: str
    asset_class: AssetClass
    market: str
    exchange: str
    symbol: str
    effective_from: date
    provenance: Provenance
    isin: str | None = None
    provider_token: str | None = None
    effective_to: date | None = None          # last day this version held, inclusive; None = still open
    underlying: str | None = None             # uid of the underlying instrument or index

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_class", AssetClass(self.asset_class))
        if not (self.uid and self.market and self.exchange and self.symbol):
            raise InstrumentError("uid, market, exchange and symbol are required")
        if self.isin is not None and not _ISIN.match(self.isin):
            raise InstrumentError(f"malformed ISIN {self.isin!r}")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise InstrumentError("effective_to is before effective_from")
        derived = self.asset_class in (AssetClass.OPTION, AssetClass.FUTURE)
        if derived and not self.underlying:
            raise InstrumentError(f"{self.asset_class.value} needs an underlying")
        if self.underlying == self.uid:
            raise InstrumentError("an instrument cannot be its own underlying")

    def covers(self, day: date) -> bool:
        return self.effective_from <= day and (self.effective_to is None or day <= self.effective_to)

    def overlaps(self, other: InstrumentId) -> bool:
        a_end = self.effective_to or date.max
        b_end = other.effective_to or date.max
        return self.effective_from <= b_end and other.effective_from <= a_end


@dataclass(frozen=True)
class Terms:
    """Contract or scheme terms effective from a date. Only the fields that apply are set."""

    uid: str
    effective_from: date
    provenance: Provenance
    tick: Decimal | None = None
    lot: int | None = None
    multiplier: Decimal | None = None
    expiry: date | None = None
    strike: Decimal | None = None
    option_type: str | None = None            # CE or PE
    benchmark: str | None = None              # an ETF's or scheme's index; prefer the total-return index
    mf_plan: str | None = None                # DIRECT or REGULAR
    mf_option: str | None = None              # GROWTH, IDCW_PAYOUT, IDCW_REINVEST
    unit_places: int | None = None

    def __post_init__(self) -> None:
        for name in ("tick", "multiplier", "strike"):
            v = getattr(self, name)
            if v is not None:
                d = to_decimal(v)
                if d <= 0:
                    raise InstrumentError(f"{name} must be positive")
                object.__setattr__(self, name, d)
        if self.lot is not None and (isinstance(self.lot, bool) or self.lot < 1):
            raise InstrumentError("lot must be at least 1")
        if self.option_type is not None and self.option_type not in ("CE", "PE"):
            raise InstrumentError("option_type must be CE or PE")


@dataclass(frozen=True)
class StateChange:
    uid: str
    effective_from: date
    state: InstrumentState
    provenance: Provenance

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", InstrumentState(self.state))


@dataclass(frozen=True)
class Succession:
    """One instrument continuing as another: an ISIN change on a split, a scheme merger, a rename.

    ``ratio`` is new units received per old unit. The old ``uid`` is kept, so anything recorded against it
    still resolves to what it was on its own date.
    """

    old_uid: str
    new_uid: str
    effective_date: date
    kind: str
    provenance: Provenance
    ratio: Decimal = Decimal(1)

    def __post_init__(self) -> None:
        if self.kind not in ("ISIN_CHANGE", "SCHEME_MERGER", "RENAME"):
            raise InstrumentError(f"unknown succession kind {self.kind!r}")
        if self.old_uid == self.new_uid:
            raise InstrumentError("an instrument cannot succeed itself")
        r = to_decimal(self.ratio)
        if r <= 0:
            raise InstrumentError("succession ratio must be positive")
        object.__setattr__(self, "ratio", r)


class InstrumentMaster:
    def __init__(self) -> None:
        self._ids: dict[str, list[InstrumentId]] = {}
        self._terms: dict[str, list[Terms]] = {}
        self._states: dict[str, list[StateChange]] = {}
        self._succ: list[Succession] = []

    # ------------------------------------------------------------------ writes
    @staticmethod
    def _not_future(prov: Provenance, now: datetime) -> None:
        if prov.published_at > utc(now):
            raise InstrumentError(
                f"record from {prov.source} is published at {prov.published_at.isoformat()}, after the "
                f"reference clock; future-published data is refused")

    def _known(self, uid: str) -> None:
        if uid not in self._ids:
            raise InstrumentError(f"unknown instrument {uid!r}")

    def add_identity(self, item: InstrumentId, *, now: datetime) -> None:
        self._not_future(item.provenance, now)
        for other in self._ids.get(item.uid, []):
            if other.asset_class is not item.asset_class:
                raise InstrumentError(f"{item.uid} cannot change asset class")
            if other.exchange == item.exchange and other.overlaps(item):
                raise InstrumentError(f"{item.uid} already has an identity on {item.exchange} "
                                      f"overlapping {item.effective_from.isoformat()}")
        for uid, versions in self._ids.items():
            if uid == item.uid:
                continue
            for other in versions:
                if not other.overlaps(item):
                    continue
                if other.exchange == item.exchange and other.symbol == item.symbol:
                    raise InstrumentError(f"{item.exchange}:{item.symbol} is already active as {uid} "
                                          f"over an overlapping period")
                if item.isin and other.isin == item.isin:
                    raise InstrumentError(f"ISIN {item.isin} is already active as {uid}")
        self._ids.setdefault(item.uid, []).append(item)
        self._ids[item.uid].sort(key=lambda v: (v.effective_from, v.exchange))

    def add_terms(self, item: Terms, *, now: datetime) -> None:
        self._known(item.uid)
        self._not_future(item.provenance, now)
        self._terms.setdefault(item.uid, []).append(item)
        self._terms[item.uid].sort(key=lambda t: (t.effective_from, t.provenance.published_at))

    def add_state(self, item: StateChange, *, now: datetime) -> None:
        self._known(item.uid)
        self._not_future(item.provenance, now)
        self._states.setdefault(item.uid, []).append(item)
        self._states[item.uid].sort(key=lambda s: (s.effective_from, s.provenance.published_at))

    def add_succession(self, item: Succession, *, now: datetime) -> None:
        self._known(item.old_uid)
        self._known(item.new_uid)
        self._not_future(item.provenance, now)
        if any(s.old_uid == item.old_uid for s in self._succ):
            raise InstrumentError(f"{item.old_uid} already has a successor")
        if item.old_uid in self._forward(item.new_uid):
            raise InstrumentError("succession would form a cycle")
        self._succ.append(item)

    # ------------------------------------------------------------------- reads
    @staticmethod
    def _visible(prov: Provenance, known_at: datetime | None) -> bool:
        return known_at is None or prov.published_at <= utc(known_at)

    def resolve(self, exchange: str, symbol: str, on: date, *,
                known_at: datetime | None = None) -> InstrumentId:
        """The instrument a symbol meant on a date, as it could have been known at ``known_at``."""
        hits = [v for versions in self._ids.values() for v in versions
                if v.exchange == exchange and v.symbol == symbol and v.covers(on)
                and self._visible(v.provenance, known_at)]
        if not hits:
            raise InstrumentError(f"unknown instrument {exchange}:{symbol} on {on.isoformat()}; "
                                  f"no trade is possible")
        if len(hits) > 1:
            raise InstrumentError(f"{exchange}:{symbol} is ambiguous on {on.isoformat()}")
        return hits[0]

    def identity(self, uid: str, on: date, *, exchange: str | None = None,
                 known_at: datetime | None = None) -> InstrumentId:
        self._known(uid)
        hits = [v for v in self._ids[uid] if v.covers(on) and self._visible(v.provenance, known_at)
                and (exchange is None or v.exchange == exchange)]
        if not hits:
            raise InstrumentError(f"{uid} has no identity on {on.isoformat()}")
        if len(hits) > 1:
            raise InstrumentError(f"{uid} is listed on several exchanges on {on.isoformat()}; name one")
        return hits[0]

    def terms(self, uid: str, on: date, *, known_at: datetime | None = None) -> Terms:
        self._known(uid)
        hits = [t for t in self._terms.get(uid, [])
                if t.effective_from <= on and self._visible(t.provenance, known_at)]
        if not hits:
            raise InstrumentError(f"no terms for {uid} effective on {on.isoformat()}; refusing to guess")
        return hits[-1]

    def state(self, uid: str, on: date, *, known_at: datetime | None = None) -> InstrumentState | None:
        """The state on a date, or None when no state has been recorded (which blocks trading)."""
        self._known(uid)
        hits = [s for s in self._states.get(uid, [])
                if s.effective_from <= on and self._visible(s.provenance, known_at)]
        return hits[-1].state if hits else None

    def _gate(self, uid: str, on: date, known_at: datetime | None,
              allowed: frozenset[InstrumentState]) -> tuple[bool, str]:
        try:
            ident = [v for v in self._ids.get(uid, []) if v.covers(on)
                     and self._visible(v.provenance, known_at)]
        except ClockError as exc:
            return False, str(exc)
        if not ident:
            return False, "UNKNOWN_IDENTITY"
        if ident[0].asset_class is AssetClass.INDEX:
            return False, "INDEX_IS_NOT_TRADABLE"
        try:
            expiry = self.terms(uid, on, known_at=known_at).expiry
        except InstrumentError:
            expiry = None
            if ident[0].asset_class in (AssetClass.OPTION, AssetClass.FUTURE):
                return False, "TERMS_UNKNOWN"
        if expiry is not None and on > expiry:
            return False, "MATURED"
        state = self.state(uid, on, known_at=known_at)
        if state is None:
            return False, "STATE_UNKNOWN"
        return (True, "OK") if state in allowed else (False, f"STATE_{state.value}")

    def can_enter(self, uid: str, on: date, *, known_at: datetime | None = None) -> tuple[bool, str]:
        """Whether a new purchase is possible. Fails closed on anything unknown."""
        return self._gate(uid, on, known_at, _ENTRY_OK)

    def can_exit(self, uid: str, on: date, *, known_at: datetime | None = None) -> tuple[bool, str]:
        """Whether a sale or redemption is possible. A blocked exit is a fact to report, not to hide."""
        return self._gate(uid, on, known_at, _EXIT_OK)

    def _forward(self, uid: str) -> list[str]:
        chain: list[str] = []
        seen = {uid}
        while True:
            nxt = next((s.new_uid for s in self._succ if s.old_uid == uid), None)
            if nxt is None or nxt in seen:
                return chain
            chain.append(nxt)
            seen.add(nxt)
            uid = nxt

    def successions(self, uid: str, *, after: date | None = None, through: date | None = None,
                    known_at: datetime | None = None) -> list[Succession]:
        """Successions from ``uid`` onward, in order, effective in ``(after, through]``."""
        out: list[Succession] = []
        seen = {uid}
        while True:
            step = next((s for s in self._succ if s.old_uid == uid
                         and self._visible(s.provenance, known_at)), None)
            if step is None or step.new_uid in seen:
                return out
            if through is not None and step.effective_date > through:
                return out
            if after is None or step.effective_date > after:
                out.append(step)
            seen.add(step.new_uid)
            uid = step.new_uid

    def current(self, uid: str, on: date, *, known_at: datetime | None = None) -> str:
        """The uid that ``uid`` has become by a date. Before any succession it is itself."""
        self._known(uid)
        steps = self.successions(uid, through=on, known_at=known_at)
        return steps[-1].new_uid if steps else uid

    def lineage(self, uid: str) -> list[str]:
        """Every uid in the chain, oldest first."""
        self._known(uid)
        back, cur = [], uid
        while True:
            prev = next((s.old_uid for s in self._succ if s.new_uid == cur), None)
            if prev is None or prev in back or prev == uid:
                break
            back.append(prev)
            cur = prev
        return list(reversed(back)) + [uid] + self._forward(uid)

    def underlying_chain(self, uid: str, on: date, *, known_at: datetime | None = None) -> list[str]:
        """Underlyings from the instrument down to its root (option -> index, future -> stock)."""
        chain: list[str] = []
        cur = uid
        while True:
            versions = [v for v in self._ids.get(cur, []) if v.covers(on)
                        and self._visible(v.provenance, known_at)]
            if not versions:
                raise InstrumentError(f"unknown instrument {cur!r} on {on.isoformat()}")
            nxt = versions[0].underlying
            if nxt is None:
                return chain
            if nxt in chain or nxt == uid:
                raise InstrumentError("underlying lineage forms a cycle")
            chain.append(nxt)
            cur = nxt

    # ------------------------------------------------------------------ dumps
    def records(self) -> dict[str, list]:
        return {"identity": [v for vs in self._ids.values() for v in vs],
                "terms": [t for ts in self._terms.values() for t in ts],
                "state": [s for ss in self._states.values() for s in ss],
                "succession": list(self._succ)}

    def __len__(self) -> int:
        return len(self._ids)
