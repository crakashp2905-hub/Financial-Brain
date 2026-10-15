"""The OMS: an intent becomes an order, the order passes the gate and the stage, goes to the broker, and every change
the broker reports is journaled exactly once.

    submit(intent)       build the order (deterministic client id - submitting the same intent twice returns the
                         first order), run the risk gate, then by stage: AWAITING_APPROVAL (TICKET) or send
    approve(id, actor)   a human releases an AWAITING_APPROVAL order; an ``agent:`` actor is refused
    poll()               read the broker's order book and move each live order to what the broker says
    cycle()              poll, then reconcile; a mismatch halts (kill switch + one stage down)
    end_of_day()         cancel what is still working, expire unapproved tickets, reconcile

The broker is the truth for an order's state: the journal follows it, never the other way. An order whose placement
timed out is UNKNOWN until the broker's book shows its tag; after ``UNKNOWN_GRACE`` polls without it, NOT_FOUND.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from . import killswitch as KS
from . import model as M
from . import reconcile as RC
from . import risk_gate as RG
from . import rollout as RO
from .broker import BrokerError, BrokerTimeout

UNKNOWN_GRACE = 3
PAPER_LIMITS = RO.SMALL


class OMS:
    def __init__(self, journal, broker, *, verdicts: dict[str, str], stage_path=None, ks_path=None,
                 banned: frozenset | None = None, baseline: dict | None = None, clock=None,
                 paper_limits: RG.Limits = PAPER_LIMITS):
        self.j, self.b = journal, broker
        self.verdicts = verdicts
        self.stage_path, self.ks_path = stage_path, ks_path
        self.banned = banned                       # None = the ban list could not be read: every symbol refused
        self.baseline = baseline or {}
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.paper_limits = paper_limits

    # ------------------------------------------------------------------ helpers

    @property
    def live_broker(self) -> bool:
        return getattr(self.b, "name", "") != "paper"

    def _stage(self) -> dict:
        return RO.load(self.stage_path)

    def _limits(self, strategy: str | None = None) -> RG.Limits | None:
        st = self._stage()
        verdict = self.verdicts.get(strategy) if strategy else None
        if not self.live_broker:
            return RO.limits(st, verdict) or self.paper_limits
        return RO.limits(st, verdict)

    def _context(self, o: M.Order, ltp: float | None) -> RG.Context:
        today = [x for x in self.j.orders(day=o.day) if x.state not in (M.REJECTED_RISK, M.NEW)]
        pos = self.j.positions()
        k = (o.exchange, o.symbol)
        held = {kk: q for kk, q in pos.items()}
        for kk, q in self.baseline.items():
            held[kk] = held.get(kk, 0) + q
        prices = {kk: ltp for kk in [k]} if ltp else {}
        gross = sum(abs(q) * (prices.get(kk) or self._last_price(kk)) for kk, q in held.items())
        sent = [datetime.fromisoformat(e["at"]) for e in self.j.events() if e["state"] in (M.SUBMITTED, M.UNKNOWN)]
        return RG.Context(now=self.clock(), ltp=ltp, position_qty=held.get(k, 0), gross_inr=gross,
                          orders_today=len(today), notional_today=sum(x.notional for x in today),
                          live_same_side=any(x.symbol == o.symbol and x.side == o.side and x.state in M.LIVE | {M.AWAITING_APPROVAL, M.APPROVED}
                                             for x in today),
                          recent_order_times=sent, banned=self.banned if self.banned is not None else frozenset({o.symbol}))

    def _last_price(self, k) -> float:
        fills = [o for o in self.j.orders() if (o.exchange, o.symbol) == k and o.avg_price]
        return fills[-1].avg_price if fills else 0.0

    def _save(self, o: M.Order, event: str, extra: str = "") -> M.Order:
        self.j.record(o, event, key_extra=extra)
        return o

    # ------------------------------------------------------------------ intents

    def submit(self, *, strategy: str, day: str, symbol: str, side: str, qty: int, limit_price: float,
               exchange: str = "NSE", product: str = "CNC", arrival_price: float | None = None,
               slice_no: int = 0, parent_id: str | None = None) -> M.Order:
        cid = M.client_id(strategy, day, symbol, side, qty, slice_no)
        existing = self.j.get(cid)
        if existing is not None:
            return existing                                # idempotent: the same intent is the same order
        o = M.Order(client_id=cid, strategy=strategy, day=day, symbol=symbol, exchange=exchange, side=side, qty=qty,
                    limit_price=limit_price, product=product, parent_id=parent_id,
                    arrival_price=arrival_price or limit_price)
        self._save(o, "CREATED")
        st = self._stage()
        lim = self._limits(strategy)
        why = []
        if lim is None:
            why.append(f"rollout stage {RO.NAMES[st['stage']]}: no live orders")
        if self.live_broker and not RO.eligible(st["stage"], self.verdicts.get(strategy)):
            why.append(f"strategy {strategy} is {self.verdicts.get(strategy)}; stage {RO.NAMES[st['stage']]} needs "
                       f"{RO.REQUIRED_VERDICT[st['stage']]}")
        if KS.load(self.ks_path).get("tripped"):
            why.append("kill switch tripped")
        if self.banned is None:
            why.append("today's F&O ban list could not be read")
        if lim is not None:
            ltp = self.b.ltp([(exchange, symbol)]).get((exchange, symbol))
            why += RG.check(o, lim, self._context(o, ltp))
        if why:
            return self._save(M.advance(o, M.REJECTED_RISK, reason="; ".join(why)), "RISK_REJECTED")
        if st["stage"] == RO.TICKET:
            return self._save(M.advance(o, M.AWAITING_APPROVAL), "AWAITING_APPROVAL")
        return self._send(self._save(M.advance(o, M.APPROVED), "AUTO_APPROVED"))

    def approve(self, client_id: str, actor: str) -> M.Order:
        if not actor or actor.startswith("agent:"):
            raise PermissionError("only a human may approve an order")
        o = self.j.get(client_id)
        if o is None or o.state != M.AWAITING_APPROVAL:
            raise M.OrderError(f"{client_id} is not awaiting approval")
        o = self._save(M.advance(o, M.APPROVED, meta={**o.meta, "approved_by": actor}), "HUMAN_APPROVED")
        return self._send(o)

    def decline(self, client_id: str, actor: str) -> M.Order:
        o = self.j.get(client_id)
        if o is None or o.state != M.AWAITING_APPROVAL:
            raise M.OrderError(f"{client_id} is not awaiting approval")
        return self._save(M.advance(o, M.CANCELLED, reason=f"declined by {actor}"), "DECLINED")

    def _send(self, o: M.Order) -> M.Order:
        if KS.load(self.ks_path).get("tripped"):
            return self._save(M.advance(o, M.CANCELLED, reason="kill switch tripped before sending"), "BLOCKED")
        try:
            bid = self.b.place(o)
        except BrokerTimeout as e:
            return self._save(M.advance(o, M.UNKNOWN, reason=str(e)), "PLACE_TIMEOUT")
        except BrokerError as e:
            return self._save(M.advance(o, M.REJECTED, reason=str(e)), "BROKER_REFUSED")
        except Exception as e:                             # a closed lock (LiveLocked) or anything else: not sent
            return self._save(M.advance(o, M.CANCELLED, reason=f"not sent: {e}"), "NOT_SENT")
        return self._save(M.advance(o, M.SUBMITTED, broker_id=bid), "SUBMITTED")

    # ------------------------------------------------------------------ the broker's view

    def poll(self) -> list[M.Order]:
        book = self.b.orders()
        by_id = {x.broker_id: x for x in book}
        by_tag = {x.tag: x for x in book if x.tag}
        changed = []
        for o in self.j.orders(states=M.LIVE):
            snap = by_id.get(o.broker_id) if o.broker_id else by_tag.get(o.client_id)
            if snap is None:
                if o.state == M.UNKNOWN:
                    n = int(o.meta.get("unknown_polls", 0)) + 1
                    if n >= UNKNOWN_GRACE:
                        changed.append(self._save(M.advance(o, M.NOT_FOUND, reason=f"absent from the broker's book "
                                                            f"after {n} polls"), "NOT_FOUND"))
                    else:
                        self._save(replace(o, meta={**o.meta, "unknown_polls": n}), "UNKNOWN_POLL", extra=str(n))
                continue
            to = snap.state
            if to == o.state and snap.filled_qty == o.filled_qty:
                continue
            if to == M.CANCELLED and snap.filled_qty and o.state in (M.SUBMITTED, M.UNKNOWN, M.OPEN) and snap.filled_qty < o.qty:
                o = self._save(M.advance(o, M.PARTIAL, filled_qty=snap.filled_qty, avg_price=snap.avg_price,
                                         broker_id=snap.broker_id), "BROKER_UPDATE", extra=str(snap.updated))
            o2 = M.advance(o, to, filled_qty=snap.filled_qty, avg_price=snap.avg_price, broker_id=snap.broker_id,
                           reason=snap.message or o.reason)
            if o2 is not o:
                changed.append(self._save(o2, "BROKER_UPDATE", extra=str(snap.updated)))
        return changed

    def cycle(self) -> dict:
        changed = self.poll()
        rec = RC.run(self.j, self.b, baseline=self.baseline, stage_path=self.stage_path, ks_path=self.ks_path,
                     halt=self.live_broker)
        chain = self.j.verify()
        if not chain["ok"] and self.live_broker:
            RO.demote(f"journal hash chain broken at {chain.get('broken_at')}", path=self.stage_path, journal=self.j,
                      broker=self.b, ks_path=self.ks_path)
        return {"changed": len(changed), "reconciled": rec["ok"], "chain": chain["ok"]}

    def end_of_day(self, day: str) -> dict:
        for o in self.j.orders(states={M.AWAITING_APPROVAL}, day=day):
            self._save(M.advance(o, M.CANCELLED, reason="session ended unapproved"), "EXPIRED_TICKET")
        for o in self.j.orders(states={M.OPEN, M.PARTIAL, M.SUBMITTED}, day=day):
            if o.broker_id:
                try:
                    self.b.cancel(o.broker_id)
                except BrokerError:
                    pass                                   # the poll below records whatever the broker says
        self.poll()
        return self.cycle()
