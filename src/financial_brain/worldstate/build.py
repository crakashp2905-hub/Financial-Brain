"""World state (C08) - one immutable, cited snapshot per session.

ARCHITECTURE.md §6: agents reason from a versioned world state, not by browsing and
improvising, and every decision references exactly one version. A snapshot here is:

    market      regime (with its reasons), key indices, India VIX, breadth
    sectors     sector indices: day and 20-session change
    movers      largest liquid gainers and losers, corporate-action days excluded
    events      high-materiality announcements in the window
    upcoming    reported corporate actions with an ex-date in the next 7 days
    quality     what is missing or partial, stated rather than hidden

**Time.** The state for session D covers the market's close on D and every event
published in the window (D 09:00, next session 09:00] IST - i.e. what a reader of the
next morning's brief could know. Nothing published after ``as_of`` can enter it.

**Immutable, content-addressed.** ``version_id`` hashes the content; rebuilding identical
content yields the same version, and anything different is a new one. Every item cites
evidence, and every cited claim is recorded in ``evidence_use``.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone

from ..config import TIER
from ..events.classify import legal_tone
from ..evidence import ledger

BUILDER = "worldstate v4"                  # v4: + model-read tone (ADR-0002)
KEY_INDICES = ["Nifty 50", "Nifty Bank", "Nifty 500", "NIFTY Midcap 100",
               "NIFTY Smallcap 100", "India VIX"]
SECTORS = ["Nifty IT", "Nifty Auto", "Nifty FMCG", "Nifty Pharma", "Nifty Metal",
           "Nifty Realty", "Nifty PSU Bank", "Nifty Energy", "Nifty Financial Services",
           "Nifty Media"]
MOVER_MIN_TURNOVER = 1e8          # Rs 10 crore
CLOSE = time(15, 30)
PRE_OPEN = time(9, 0)


def _sessions(con, d: date):
    prev = con.execute("SELECT MAX(business_date) FROM universe_snapshots "
                       "WHERE exchange = 'NSE' AND business_date < ?", [d]).fetchone()[0]
    nxt = con.execute("SELECT MIN(business_date) FROM universe_snapshots "
                      "WHERE exchange = 'NSE' AND business_date > ?", [d]).fetchone()[0]
    return prev, nxt


def _lake_key(con, dataset: str, source: str, d: date):
    r = con.execute("""SELECT lake_key FROM ingest_runs WHERE dataset = ? AND source = ?
                       AND business_date = ? AND status IN ('ok', 'ok_partial')
                       AND lake_key IS NOT NULL ORDER BY started_at DESC LIMIT 1""",
                    [dataset, source, d]).fetchone()
    return r[0] if r else None


def build(con, d: date, *, as_of: datetime | None = None) -> dict:
    prev, nxt = _sessions(con, d)
    if prev is None:
        raise ValueError(f"no previous session before {d}")
    as_of = as_of or datetime.combine(nxt or d + timedelta(days=1), PRE_OPEN)
    window_start = datetime.combine(d, PRE_OPEN)
    close_ts = datetime.combine(d, CLOSE)
    cited: list[str] = []
    quality: list[str] = []

    # ---- indices -------------------------------------------------------------
    idx_key = _lake_key(con, "index_close", "NSE", d)
    if not idx_key:
        quality.append(f"no index file held for {d}")

    def index_item(name):
        rows = con.execute("""
            WITH s AS (SELECT business_date, close_level FROM index_levels_canonical
                       WHERE index_name = ? AND variant = 'PRICE' AND business_date <= ?
                       ORDER BY business_date DESC LIMIT 21)
            SELECT business_date, close_level FROM s ORDER BY business_date DESC""",
                           [name, d]).fetchall()
        if not rows or rows[0][0] != d:
            missing_levels.append(name)
            return None
        close = rows[0][1]
        chg1 = close / rows[1][1] - 1 if len(rows) > 1 else None
        chg20 = close / rows[-1][1] - 1 if len(rows) >= 21 else None
        eid = ledger.mint(con, kind="index_close", subject=name, as_of=close_ts,
                          claim=f"{name} closed at {close:,.2f} on {d}",
                          value={"close": close, "chg_1d": chg1, "chg_20d": chg20},
                          source="NSE", source_tier=TIER["NSE"], lake_key=idx_key,
                          derivation="ind_close_all via index_levels_canonical")
        cited.append(eid)
        return {"name": name, "close": close, "chg_1d": chg1, "chg_20d": chg20,
                "evidence": eid}

    missing_levels: list[str] = []
    indices = [i for i in (index_item(n) for n in KEY_INDICES) if i]
    sectors = sorted([i for i in (index_item(n) for n in SECTORS) if i],
                     key=lambda i: i["chg_1d"] or 0, reverse=True)
    if missing_levels:   # one note, not fifteen
        quality.append(f"{len(missing_levels)} indices have no level for {d}: "
                       + ", ".join(missing_levels))

    # ---- market regime -------------------------------------------------------
    bhav_key = _lake_key(con, "bhavcopy_cm", "NSE", d)
    reg = con.execute("""SELECT regime, raw_regime, reasons, breadth_200, advances, declines,
                                vix, drawdown, version
                         FROM market_regime WHERE business_date = ?
                         ORDER BY version DESC LIMIT 1""", [d]).fetchone()
    market = None
    if reg:
        breadth_ev = ledger.mint(
            con, kind="breadth", subject="NSE EQ", as_of=close_ts,
            claim=f"{reg[4]} advances, {reg[5]} declines; {reg[3]:.0%} of NSE stocks above "
                  f"their 200-session average on {d}" if reg[3] is not None else
                  f"{reg[4]} advances, {reg[5]} declines on {d}",
            value={"advances": reg[4], "declines": reg[5], "above_200": reg[3]},
            source="NSE", source_tier=TIER["NSE"], lake_key=bhav_key,
            derivation="market_breadth over universe_snapshots")
        inputs = [breadth_ev] + [i["evidence"] for i in indices
                                 if i["name"] in ("Nifty 50", "India VIX")]
        reg_ev = ledger.mint(
            con, kind="regime", subject="MARKET", as_of=close_ts,
            claim=f"Market regime {reg[0]} on {d}: {reg[2]}",
            value={"regime": reg[0], "raw": reg[1], "reasons": reg[2]},
            source="Financial-Brain", source_tier=TIER["NSE"],
            derivation=f"regime/brain {reg[8]}", inputs=inputs)
        cited += [breadth_ev, reg_ev]
        market = {"regime": reg[0], "raw_regime": reg[1], "reasons": reg[2],
                  "advances": reg[4], "declines": reg[5], "breadth_200": reg[3],
                  "vix": reg[6], "drawdown": reg[7], "version": reg[8],
                  "evidence": reg_ev, "breadth_evidence": breadth_ev}
    else:
        quality.append(f"no market regime computed for {d}")

    # ---- movers (corporate-action days excluded: a split is not a 50% fall) ---
    movers = []
    for isin, ticker, close, prev_close, turnover in con.execute("""
        WITH t AS (SELECT isin, ticker, close_price, turnover FROM universe_snapshots
                   WHERE exchange = 'NSE' AND series = 'EQ' AND business_date = ?),
             y AS (SELECT isin, close_price FROM universe_snapshots
                   WHERE exchange = 'NSE' AND series = 'EQ' AND business_date = ?)
        SELECT t.isin, t.ticker, t.close_price, y.close_price, t.turnover
        FROM t JOIN y USING (isin)
        -- Equity shares only (ISIN type 01): ETFs on foreign indices (MONQ50, MASPTOP50)
        -- topped the gainers, and they are not Indian equity news.
        WHERE t.turnover >= ? AND y.close_price > 0
          AND t.isin LIKE 'INE%' AND substr(t.isin, 8, 2) = '01'
          AND NOT EXISTS (SELECT 1 FROM corporate_actions ca WHERE ca.isin = t.isin
                          AND ca.ex_date = ? AND (ca.ratio_to IS NOT NULL
                                                  OR ca.derived_factor IS NOT NULL))
        ORDER BY ABS(t.close_price / y.close_price - 1) DESC LIMIT 20""",
            [d, prev, MOVER_MIN_TURNOVER, d]).fetchall():
        pct = close / prev_close - 1
        eid = ledger.mint(con, kind="price_move", subject=isin, as_of=close_ts,
                          claim=f"{ticker} closed {pct:+.1%} at {close:,.2f} on {d} "
                                f"(turnover Rs {turnover / 1e7:,.0f} cr)",
                          value={"ticker": ticker, "close": close, "prev_close": prev_close,
                                 "pct": pct, "turnover": turnover},
                          source="NSE", source_tier=TIER["NSE"], lake_key=bhav_key,
                          derivation="universe_snapshots close/prev close, EQ series")
        cited.append(eid)
        movers.append({"isin": isin, "ticker": ticker, "close": close, "pct": pct,
                       "turnover": turnover, "evidence": eid})
    gainers = sorted([m for m in movers if m["pct"] > 0], key=lambda m: -m["pct"])[:8]
    losers = sorted([m for m in movers if m["pct"] < 0], key=lambda m: m["pct"])[:8]

    # ---- events: high-materiality announcements in the window ------------------
    # One helper mints every announcement, so the same filing is always the same
    # evidence_id wherever it is cited (it was minted twice with different fields).
    ann_cols = ("news_id, isin, company, event_type, materiality, headline, subject, "
                "published_at, evidence_key")

    def announcement(row) -> dict:
        nid, isin, company, kind, mat, headline, subject, published, key = row
        text = (headline or "").strip()
        # "As enclosed" / "Enclosed" say nothing; BSE's subject line then carries the
        # company, scrip and subcategory, which does.
        if len(text) < 25:
            text = (subject or text).strip()
        eid = ledger.mint(con, kind="announcement", subject=isin or company,
                          as_of=published, published_at=published,
                          claim=f"{company}: {kind} - {text[:240]}",
                          value={"news_id": nid, "event_type": kind, "company": company},
                          source="BSE", source_tier=TIER["BSE"], lake_key=key,
                          derivation="BSE announcements, events/classify")
        cited.append(eid)
        return {"news_id": nid, "isin": isin, "company": company, "event_type": kind,
                "materiality": mat, "text": text, "published_at": published,
                "tone": legal_tone(text) if kind == "LEGAL_REGULATORY" else None,
                "evidence": eid}

    events = [announcement(r) for r in con.execute(f"""
        SELECT {ann_cols} FROM announcements
        WHERE materiality = 'high' AND published_at > ? AND published_at <= ?
        ORDER BY published_at""", [window_start, as_of]).fetchall()]

    # ---- shareholder tone read by a model (ADR-0002) - only answers that cleared
    #      the answering model's calibrated bar; the claim names the model -------------
    if events and con.execute("""SELECT 1 FROM information_schema.tables
                                 WHERE table_name = 'announcement_tone'""").fetchone():
        tones = {nid: row for nid, *row in con.execute(
            f"""SELECT news_id, tone, confidence, model, COALESCE(text_source, 'filing')
                FROM announcement_tone
                WHERE accepted AND tone <> 'neutral' AND news_id IN
                ({','.join('?' * len(events))})""", [e["news_id"] for e in events]
        ).fetchall()}
        for e in events:
            if e["news_id"] in tones:
                tone, conf, model, src = tones[e["news_id"]]
                what = "the news it quotes" if src == "news_headline" else "filing"
                tid = ledger.mint(
                    con, kind="tone", subject=e["isin"] or e["company"],
                    as_of=e["published_at"],
                    claim=f"{e['company']}: {what} reads {tone} for shareholders "
                          f"({model}, confidence {conf:.2f})",
                    value={"news_id": e["news_id"], "tone": tone, "model": model,
                           "confidence": round(conf, 4), "text_source": src},
                    source="MODEL", source_tier=TIER["MODEL"],
                    derivation=f"llm/router sentiment via {model} on {src}",
                    inputs=[e["evidence"]])
                cited.append(tid)
                e["model_tone"] = {"tone": tone, "model": model, "evidence": tid,
                                   "text_source": src}

    # ---- the news a filing refers to, recovered from the filing's own text ----------
    #      Tier DERIVED: a deterministic rule over Tier-1 bytes, not a fetched article.
    if events and con.execute("""SELECT 1 FROM information_schema.tables
                                 WHERE table_name = 'announcement_news'""").fetchone():
        refs = {nid: row for nid, *row in con.execute(
            f"""SELECT news_id, headline, domain, url, how FROM announcement_news
                WHERE headline IS NOT NULL AND news_id IN
                ({','.join('?' * len(events))})""", [e["news_id"] for e in events]
        ).fetchall()}
        for e in events:
            if e["news_id"] in refs:
                headline, domain, url, how = refs[e["news_id"]]
                nid_ = ledger.mint(
                    con, kind="news_reference", subject=e["isin"] or e["company"],
                    as_of=e["published_at"],
                    claim=f"{e['company']}: the filing refers to a news item"
                          + (f" on {domain}" if domain else "") + f': "{headline}"',
                    value={"news_id": e["news_id"], "headline": headline,
                           "domain": domain, "url": url, "how": how},
                    source="DERIVED", source_tier=TIER["DERIVED"],
                    derivation=f"events/newsref {how}", inputs=[e["evidence"]])
                cited.append(nid_)
                e["news_ref"] = {"headline": headline, "domain": domain,
                                 "evidence": nid_}

    # ---- group contagion: a red flag in one company touches its promoter group ----
    red = {"INSOLVENCY", "AUDITOR_RESIGNATION", "PROMOTER_PLEDGE", "LEGAL_REGULATORY"}
    flagged = [e for e in events if e["event_type"] in red and e["isin"]
               and e.get("tone") != "favourable"]
    if flagged and con.execute("""SELECT 1 FROM information_schema.tables
                                  WHERE table_name = 'holder_filings'""").fetchone():
        from ..graph import build as graph
        member_of = {i: g for g in graph.groups(con, as_of=d) for i in g["isins"]}
        for e in flagged:
            g = member_of.get(e["isin"])
            if not g:
                continue
            siblings = [name for m, name in zip(g["members"], g["companies"])
                        if e["isin"] not in g["member_isins"][m]]
            gid = ledger.mint(
                con, kind="promoter_group", subject=e["isin"], as_of=close_ts,
                claim=f"{e['company']} is in the {g['anchor'] or 'unnamed'} promoter group "
                      f"({len(g['members'])} listed companies) as known on {d}",
                value={"group_id": g["group_id"], "anchor": g["anchor"],
                       "members": g["members"]},
                source="BSE", source_tier=TIER["BSE"],
                derivation="graph/build: corroborated Reg. 31 / Reg. 10 promoter filers",
                inputs=[e["evidence"]])
            cited.append(gid)
            e["group"] = {"anchor": g["anchor"], "size": len(g["members"]),
                          "siblings": siblings[:6], "evidence": gid}

    # ---- why did the big movers move? their filings since the previous morning --
    for m in gainers + losers:
        m["filings"] = [announcement(r) for r in con.execute(f"""
            SELECT {ann_cols} FROM announcements
            WHERE isin = ? AND published_at > ? AND published_at <= ?
              AND materiality IN ('high', 'medium')
            ORDER BY materiality, published_at DESC LIMIT 3""",
            [m["isin"], datetime.combine(prev, PRE_OPEN), as_of]).fetchall()]
    if not con.execute("SELECT 1 FROM announcements WHERE published_at > ? LIMIT 1",
                       [window_start]).fetchone():
        quality.append("no announcements held for this window")

    # ---- upcoming reported corporate actions -----------------------------------
    upcoming = []
    for isin, kind, ex_date, details, key, rf, rt, amt in con.execute("""
        SELECT isin, action_type, ex_date, details, evidence_key, ratio_from, ratio_to, amount
        FROM corporate_actions WHERE confidence = 'reported'
          AND ex_date > ? AND ex_date <= ?
          AND action_type IN ('SPLIT', 'BONUS', 'RIGHTS', 'BUYBACK', 'SPIN_OFF', 'MERGER',
                              'CAPITAL_REDUCTION', 'DIVIDEND')
        ORDER BY ex_date, action_type""", [d, d + timedelta(days=7)]).fetchall():
        eid = ledger.mint(con, kind="corporate_action", subject=isin,
                          as_of=datetime.combine(ex_date, PRE_OPEN),
                          claim=f"{details} - ex-date {ex_date}",
                          value={"type": kind, "ex_date": ex_date, "ratio_from": rf,
                                 "ratio_to": rt, "amount": amt},
                          source="BSE", source_tier=TIER["BSE"], lake_key=key,
                          derivation="BSE corporate-action feed")
        cited.append(eid)
        upcoming.append({"isin": isin, "type": kind, "ex_date": ex_date, "details": details,
                         "amount": amt, "evidence": eid})

    content = {"business_date": d, "previous_session": prev, "as_of": as_of,
               "builder": BUILDER, "market": market, "indices": indices,
               "sectors": sectors, "gainers": gainers, "losers": losers, "events": events,
               "upcoming": upcoming, "quality": quality}
    body = json.dumps(ledger._canon(content), sort_keys=True, separators=(",", ":"))
    version_id = "ws_" + hashlib.sha256(body.encode()).hexdigest()[:24]
    con.execute("""INSERT INTO world_states (version_id, business_date, built_at, content,
                   evidence_count, builder) VALUES (?,?,?,?,?,?)
                   ON CONFLICT (version_id) DO NOTHING""",
                [version_id, d, datetime.now(timezone.utc), body, len(set(cited)), BUILDER])
    ledger.use(con, cited, used_by_kind="world_state", used_by_id=version_id)
    return {"version_id": version_id, **content}


def latest(con, d: date | None = None) -> dict | None:
    row = con.execute("""SELECT version_id, content FROM world_states
                         WHERE (? IS NULL OR business_date = ?)
                         ORDER BY business_date DESC, built_at DESC LIMIT 1""",
                      [d, d]).fetchone()
    return {"version_id": row[0], **json.loads(row[1])} if row else None
