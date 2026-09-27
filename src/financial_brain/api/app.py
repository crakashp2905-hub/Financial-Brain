"""A read-only HTTP surface over the services layer. Localhost, no orders, no approvals.

FastAPI is an **optional** dependency (``pip install -e ".[api]"``). The core install is DuckDB and
pytz, and a research system should not require a web framework to run a backtest, so the import is
deferred and its absence produces an instruction rather than a traceback.

## What this refuses to expose, and why it is refused here rather than documented

**No order path.** There is none in ``providers/`` to expose. Kite is configured read-only and a
test greps every provider file for ``/orders``, ``method="POST"`` and ``method="DELETE"``.

**No approval.** ``HUMAN_APPROVED`` is unreachable through the services layer, so it is unreachable
here. Whoever holds an API token would otherwise become "the human", which makes the approval gate
a formality.

**No writes by default.** Every route below is a GET. The two POSTs - compiling a candidate
decision and submitting a thesis challenge - are registered only when ``allow_writes=True`` is
passed explicitly at construction, and neither changes a position or a state machine: one returns
an action without storing it, the other records an adversary attempt.

**Localhost only by default.** ``serve()`` binds 127.0.0.1. A research API holding a decade of
positions and an unpublished research ledger has no business listening on 0.0.0.0, and the default
should be the safe one rather than the convenient one.

## Errors

``ServiceError`` carries a code, which maps to a status here once rather than in every route. A
caller can branch on ``error`` in the body without parsing prose.
"""
from __future__ import annotations

from datetime import date

from .. import services
from ..config import load
from ..services import companies, decisions, market, portfolio, research
from ..storage.db import Database

#: Service codes to HTTP statuses. Exhaustive over ``services``' codes by construction - a new
#: code with no mapping falls through to 500, which is the right default for "we did not think
#: about this".
STATUS = {
    services.NOT_FOUND: 404,
    services.BAD_REQUEST: 400,
    services.NOT_AVAILABLE: 501,      # the archive does not hold it - not the caller's error
    services.NOT_ESTIMABLE: 422,      # it holds it, and it is too thin to answer from
    services.FORBIDDEN: 403,
}


def _require_fastapi():
    try:
        import fastapi  # noqa: F401
    except ImportError as exc:                                  # pragma: no cover
        raise RuntimeError(
            "the API needs FastAPI, which is an optional extra: "
            'pip install -e ".[api]"'
        ) from exc
    return fastapi


def build(*, allow_writes: bool = False, config=None):
    """Construct the app. Writes are off unless asked for, explicitly, in code."""
    fastapi = _require_fastapi()
    from fastapi import HTTPException, Query
    from fastapi.responses import JSONResponse

    cfg = config or load()
    app = fastapi.FastAPI(
        title="Financial-Brain",
        description="Read-only research surface. No order path, no approval path.",
        version="0.1",
    )

    # Read-only unless writes were explicitly enabled at construction. `read_only` belongs to
    # the Database constructor, so the guarantee is set once here rather than per route - a route
    # cannot opt itself into writing.
    db = Database(cfg, read_only=not allow_writes)

    def _connect():
        return db.connect()

    @app.exception_handler(services.ServiceError)
    async def _svc(_request, exc: services.ServiceError):
        return JSONResponse(status_code=STATUS.get(exc.code, 500),
                            content=exc.as_dict())

    def run(fn, *a, **kw):
        with _connect() as con:
            try:
                return fn(con, *a, **kw)
            except services.ServiceError as exc:
                raise HTTPException(status_code=STATUS.get(exc.code, 500),
                                    detail=exc.as_dict()) from exc

    # ----------------------------------------------------------------------- market
    @app.get("/market/world-state")
    def world_state(as_of: date | None = None):
        return run(market.world_state, as_of=as_of)

    @app.get("/market/indices")
    def indices(name: list[str] | None = Query(default=None), as_of: date | None = None):
        return run(market.indices, names=name, as_of=as_of)

    @app.get("/market/regimes")
    def regimes(start: date | None = None, end: date | None = None):
        return run(market.regime_history, start=start, end=end)

    # --------------------------------------------------------------------- companies
    @app.get("/companies/resolve")
    def resolve(q: str):
        return run(companies.resolve, q)

    @app.get("/companies/{isin}")
    def company(isin: str, as_of: date | None = None):
        return run(companies.overview, isin, as_of=as_of)

    @app.get("/companies/{isin}/why")
    def why(isin: str, on: date, index: str = "Nifty 500"):
        """Decompose one session's move into market, peer and company-specific."""
        return run(companies.why_did_it_move, isin, on=on, index_name=index)

    @app.get("/companies/{isin}/peers")
    def peers(isin: str, as_of: date, k: int = 12):
        return run(companies.peer_group, isin, as_of=as_of, k=k)

    # --------------------------------------------------------------------- research
    @app.get("/research/bar")
    def bar():
        """The |t| a new result must clear, recomputed from the ledger as it stands."""
        return run(research.bar)

    @app.get("/research/ledger")
    def ledger(feature: str | None = None, limit: int = 50):
        return run(research.ledger, feature=feature, limit=min(limit, 500))

    @app.get("/research/tried")
    def tried():
        return run(research.what_has_been_tried)

    @app.get("/research/conditions")
    def conditions():
        return run(research.registered_conditions)

    @app.get("/opportunities")
    def opportunities(as_of: date, key: list[str] | None = Query(default=None),
                      per_condition: int = 10):
        return run(research.scan, as_of=as_of, keys=key,
                   limit_per_condition=min(per_condition, 50))

    # -------------------------------------------------------------------- portfolio
    @app.get("/portfolio/book")
    def book(as_of: date, window_days: int | None = 400):
        return run(portfolio.book, as_of=as_of, window_days=window_days)

    @app.get("/portfolio/risk")
    def risk(as_of: date, window_days: int | None = 400, cov_sessions: int = 250):
        return run(portfolio.risk, as_of=as_of, window_days=window_days,
                   cov_sessions=cov_sessions)

    @app.get("/portfolio/stress")
    def stress(as_of: date, window_days: int | None = 400,
               capital_inr: float | None = None):
        return run(portfolio.stress_test, as_of=as_of, window_days=window_days,
                   capital_inr=capital_inr)

    @app.get("/portfolio/limits")
    def plimits(as_of: date, window_days: int | None = 400):
        return run(portfolio.check_limits, as_of=as_of, window_days=window_days)

    @app.get("/portfolio/gate")
    def gate(isin: str, weight: float, as_of: date, capital_inr: float,
             adv_inr: float | None = None, with_stress: bool = True):
        """Can the book afford this position. A GET because it decides nothing and stores
        nothing - it reports ALLOW / RESIZE / REFUSE / ABSTAIN with the numbers behind it."""
        return run(portfolio.gate, isin=isin, weight=weight, as_of=as_of,
                   capital_inr=capital_inr, adv_inr=adv_inr, with_stress=with_stress)

    @app.get("/portfolio/signal-overlap")
    def overlap(a: str, b: str, horizon: int = 20,
                direction_a: int = 1, direction_b: int = 1):
        return run(portfolio.signal_overlap, a=a, b=b, horizon=horizon,
                   direction_a=direction_a, direction_b=direction_b)

    # -------------------------------------------------------------------- decisions
    @app.get("/decisions")
    def decision_list(state: str | None = None, action: str | None = None,
                      limit: int = 50):
        return run(decisions.listing, state=state, action=action,
                   limit=min(limit, 500))

    @app.get("/decisions/{decision_id}")
    def decision(decision_id: str):
        return run(decisions.get, decision_id)

    @app.get("/decisions/{decision_id}/quality")
    def decision_quality(decision_id: str):
        return run(decisions.score_quality, decision_id)

    if allow_writes:
        from pydantic import BaseModel

        class CompileRequest(BaseModel):
            isin: str
            as_of: date
            claims: list[dict]
            scenarios: dict | None = None
            entry: float | None = None
            invalidation: float | None = None
            capital_inr: float
            horizon_days: int = 90

        @app.post("/decisions/compile")
        def compile_decision(req: CompileRequest):
            """Resolve claims into an action. Stores nothing and places nothing - it returns
            what the compiler's precedence decided and which rule decided it."""
            return run(decisions.compile_one, isin=req.isin, as_of=req.as_of,
                       claims=req.claims, scenarios=req.scenarios, entry=req.entry,
                       invalidation=req.invalidation, capital_inr=req.capital_inr,
                       horizon_days=req.horizon_days)

        class ChallengeRequest(BaseModel):
            mechanism: str
            observable: str
            evidence: list[str] = []
            probability: float = 0.0
            author: str = "agent:adversary"

        @app.post("/decisions/{decision_id}/challenge")
        def challenge(decision_id: str, req: ChallengeRequest):
            return run(decisions.challenge, decision_id=decision_id,
                       mechanism=req.mechanism, observable=req.observable,
                       evidence=req.evidence, probability=req.probability,
                       author=req.author)

    @app.get("/health")
    def health():
        with _connect() as con:
            sessions = con.execute(
                "SELECT COUNT(DISTINCT business_date) FROM adjusted_prices").fetchone()[0]
        return {"ok": True, "sessions": sessions, "writes_enabled": allow_writes,
                "order_path": "none", "approval_path": "none"}

    return app


def serve(*, host: str = "127.0.0.1", port: int = 8848,
          allow_writes: bool = False) -> None:
    """Run the API. Binds localhost by default and says so if asked to do otherwise."""
    _require_fastapi()
    try:
        import uvicorn
    except ImportError as exc:                                  # pragma: no cover
        raise RuntimeError('the API needs uvicorn: pip install -e ".[api]"') from exc
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: binding {host} exposes an eleven-year price archive, an unpublished "
              f"research ledger and the position history to the network.")
    uvicorn.run(build(allow_writes=allow_writes), host=host, port=port)
