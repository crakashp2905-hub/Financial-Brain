"""Standalone paper-only webhook app; never mount this on the order API.

Configure ``FB_TV_TOKEN`` (at least 32 random characters), ``FB_TV_STRATEGIES``
and ``FB_TV_INBOX``. This module has no default listener or public deployment.
"""
import os
from datetime import datetime, timezone
from pathlib import Path

from .tradingview_ingress import IngressRejected, accept_alert


def build_app():
    try:
        from fastapi import FastAPI, HTTPException, Request
    except ImportError as exc:
        raise RuntimeError("install the optional api extra for webhook ingress") from exc

    token = os.environ.get("FB_TV_TOKEN", "")
    strategies = frozenset(s.strip() for s in os.environ.get("FB_TV_STRATEGIES", "").split(",") if s.strip())
    inbox = os.environ.get("FB_TV_INBOX", "")
    if len(token) < 32 or not strategies or not inbox:
        raise RuntimeError("FB_TV_TOKEN, FB_TV_STRATEGIES and FB_TV_INBOX must be configured")
    app = FastAPI(title="Financial Brain paper alert inbox", docs_url=None, redoc_url=None,
                  openapi_url=None)

    @app.post("/tv/{bearer}")
    async def alert(bearer: str, request: Request):
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise HTTPException(status_code=415, detail="unsupported content type")
        # Check the token before reading the body; bound it even if Content-Length is absent.
        from hmac import compare_digest
        if not compare_digest(bearer, token):
            raise HTTPException(status_code=403, detail="alert rejected")
        chunks = bytearray()
        async for chunk in request.stream():
            if len(chunks) + len(chunk) > 4096:
                raise HTTPException(status_code=413, detail="payload too large")
            chunks.extend(chunk)
        try:
            status, parsed = accept_alert(bytes(chunks), supplied_token=bearer, configured_token=token,
                                          database=Path(inbox), allowed_strategies=strategies,
                                          received_time=datetime.now(timezone.utc))
        except IngressRejected as exc:
            raise HTTPException(status_code=422, detail="alert rejected") from exc
        return {"status": status, "alert_id": parsed.alert_id}

    return app
