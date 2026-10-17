"""NSE's dated derivatives archives: one file per trading day, fetched to the raw lake, then loaded.

    fo_bhav        every F&O contract's day: open/high/low/close, settlement, contracts, value, open interest and its
                   change. Two formats: the old ``fo{DD}{MON}{YYYY}bhav.csv.zip`` (to 2024) and the UDiFF
                   ``BhavCopy_NSE_FO_0_0_0_{YYYYMMDD}_F_0000.csv.zip`` (from 2024-07-08). Loaded into its own file,
                   ``data/fo_history.duckdb`` (hundreds of millions of rows; the research database stays small).
    fo_ban         securities in the F&O ban period for the trade date (``fo_secban_{DDMMYYYY}.csv``)
    participant_oi open interest in equity derivatives by client type - Client, DII, FII, Pro
                   (``fao_participant_oi_{DDMMYYYY}.csv``); and ``participant_vol`` the day's volume the same way

URLs read from NSE's own "All Reports" archive pages (2026-10-18); robots.txt allows the archive host. A day the
exchange did not trade returns 404 and is recorded as such, not retried forever.
"""
from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, timedelta, timezone

import duckdb
import pandas as pd

from ..lake.store import RawLake
from ..providers.base import FetchError

ARCH = "https://nsearchives.nseindia.com"
UDIFF_FROM = date(2024, 7, 8)
FO_DB = "data/fo_history.duckdb"
KINDS = ("fo_bhav", "fo_ban", "participant_oi", "participant_vol")


def url(kind: str, d: date) -> str:
    if kind == "fo_bhav":
        if d >= UDIFF_FROM:
            return f"{ARCH}/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
        mon = d.strftime("%b").upper()
        return f"{ARCH}/content/historical/DERIVATIVES/{d:%Y}/{mon}/fo{d:%d}{mon}{d:%Y}bhav.csv.zip"
    if kind == "fo_ban":
        return f"{ARCH}/archives/fo/sec_ban/fo_secban_{d:%d%m%Y}.csv"
    if kind == "participant_oi":
        return f"{ARCH}/content/nsccl/fao_participant_oi_{d:%d%m%Y}.csv"
    if kind == "participant_vol":
        return f"{ARCH}/content/nsccl/fao_participant_vol_{d:%d%m%Y}.csv"
    raise ValueError(kind)


def _days(a: date, b: date):
    d = a
    while d <= b:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def prefetch(cfg, kinds, start: date, end: date, *, provider=None, on_progress=None) -> dict:
    from ..providers.nse_results import NSEResults
    lake, provider = RawLake(cfg.lake), provider or NSEResults(min_interval=0.4)
    out = {k: {"stored": 0, "had": 0, "missing": 0, "failed": 0} for k in kinds}
    for d in _days(start, end):
        for k in kinds:
            u = url(k, d)
            fn = u.rsplit("/", 1)[-1]
            if lake.exists("NSE", k, d, fn) or lake.exists("NSE", k, d, fn + ".404"):
                out[k]["had"] += 1
                continue
            try:
                body, status, ctype = provider._get(u)
            except FetchError as e:
                if getattr(e, "status", None) == 404:
                    lake.put(source="NSE", dataset=k, business_date=d, filename=fn + ".404", payload=b"", url=u,
                             http_status=404)
                    out[k]["missing"] += 1
                else:
                    out[k]["failed"] += 1
                continue
            lake.put(source="NSE", dataset=k, business_date=d, filename=fn, payload=body, url=u,
                     content_type=ctype or "application/octet-stream", http_status=status)
            out[k]["stored"] += 1
        if on_progress and d.day == 1:
            on_progress(d, out)
    return out


# ------------------------------------------------------------------ parsing (pure)

def _unzip(b: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        return z.read(z.namelist()[0])


def parse_fo_bhav(b: bytes, d: date) -> pd.DataFrame:
    raw = _unzip(b) if b[:2] == b"PK" else b
    df = pd.read_csv(io.BytesIO(raw), low_memory=False)
    df.columns = [c.strip() for c in df.columns]
    if "TckrSymb" in df.columns:                                     # UDiFF
        out = pd.DataFrame({
            "instrument": df["FinInstrmTp"].map({"IDF": "FUTIDX", "STF": "FUTSTK", "IDO": "OPTIDX", "STO": "OPTSTK"})
                                         .fillna(df["FinInstrmTp"]),
            "symbol": df["TckrSymb"], "expiry": pd.to_datetime(df["XpryDt"]).dt.date,
            "strike": pd.to_numeric(df["StrkPric"], errors="coerce").fillna(0.0),
            "option_type": df["OptnTp"].fillna("XX").replace("", "XX"),
            "open": df["OpnPric"], "high": df["HghPric"], "low": df["LwPric"], "close": df["ClsPric"],
            "settle": df["SttlmPric"], "contracts": df["TtlTradgVol"], "value_inr": df["TtlTrfVal"],
            "oi": df["OpnIntrst"], "chg_oi": df["ChngInOpnIntrst"], "underlying": df.get("UndrlygPric")})
    else:                                                            # the old format
        out = pd.DataFrame({
            "instrument": df["INSTRUMENT"].str.strip(), "symbol": df["SYMBOL"].str.strip(),
            "expiry": pd.to_datetime(df["EXPIRY_DT"], format="%d-%b-%Y").dt.date,
            "strike": pd.to_numeric(df["STRIKE_PR"], errors="coerce").fillna(0.0),
            "option_type": df["OPTION_TYP"].str.strip(), "open": df["OPEN"], "high": df["HIGH"], "low": df["LOW"],
            "close": df["CLOSE"], "settle": df["SETTLE_PR"], "contracts": df["CONTRACTS"],
            "value_inr": df["VAL_INLAKH"] * 1e5, "oi": df["OPEN_INT"], "chg_oi": df["CHG_IN_OI"], "underlying": None})
    out.insert(0, "trade_date", d)
    out["option_type"] = out["option_type"].where(~out["instrument"].str.startswith("FUT"), "XX")
    return out


def parse_ban(b: bytes) -> list[str]:
    lines = b.decode("utf-8", "replace").replace("\r", "\n").split("\n")
    out = []
    for ln in lines[1:]:
        parts = [p.strip() for p in ln.split(",")]
        if len(parts) >= 2 and parts[0].isdigit() and parts[1]:
            out.append(parts[1])
    return out


def parse_participant(b: bytes, d: date) -> pd.DataFrame:
    text = b.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln for ln in text.split("\n") if ln.strip()]
    i = next(k for k, ln in enumerate(lines) if ln.strip().lstrip('"').startswith("Client Type"))
    df = pd.read_csv(io.StringIO("\n".join(lines[i:])))
    df.columns = [c.strip().replace("\t", "") for c in df.columns]
    df = df.rename(columns={"Client Type": "client_type"})
    df["client_type"] = df["client_type"].astype(str).str.strip()
    long = df.melt(id_vars="client_type", var_name="measure", value_name="contracts")
    long["measure"] = long["measure"].str.strip()
    long = long[long["measure"] != ""].dropna(subset=["contracts"])
    long.insert(0, "trade_date", d)
    long["contracts"] = pd.to_numeric(long["contracts"], errors="coerce")
    return long


# ------------------------------------------------------------------ load

FO_SCHEMA = """
CREATE TABLE IF NOT EXISTS fo_bhav (
    trade_date DATE NOT NULL, instrument VARCHAR NOT NULL, symbol VARCHAR NOT NULL, expiry DATE NOT NULL,
    strike DOUBLE NOT NULL, option_type VARCHAR NOT NULL, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
    settle DOUBLE, contracts DOUBLE, value_inr DOUBLE, oi DOUBLE, chg_oi DOUBLE, underlying DOUBLE,
    PRIMARY KEY (trade_date, instrument, symbol, expiry, strike, option_type));
CREATE TABLE IF NOT EXISTS fo_loaded (trade_date DATE PRIMARY KEY, rows BIGINT, loaded_at TIMESTAMPTZ);
"""


def _objects(lake, kind: str, a: date, b: date):
    for d in _days(a, b):
        fn = url(kind, d).rsplit("/", 1)[-1]
        key = f"NSE/{kind}/{d:%Y}/{d:%m}/{d:%d}/{fn}"
        if lake.meta(key) is not None:
            yield d, key


def load_fo(cfg, start: date, end: date, *, path: str = FO_DB) -> dict:
    lake = RawLake(cfg.lake)
    con = duckdb.connect(path)
    con.execute(FO_SCHEMA)
    done = {r[0] for r in con.execute("SELECT trade_date FROM fo_loaded").fetchall()}
    out = {"days": 0, "rows": 0, "skipped": 0, "failed": 0}
    try:
        for d, key in _objects(lake, "fo_bhav", start, end):
            if d in done:
                out["skipped"] += 1
                continue
            try:
                df = parse_fo_bhav(lake.read(key), d)
            except Exception:                           # noqa: BLE001 - one bad file never stops the backfill
                out["failed"] += 1
                continue
            df = df.drop_duplicates(["instrument", "symbol", "expiry", "strike", "option_type"])
            con.register("df", df)
            con.execute("INSERT OR REPLACE INTO fo_bhav SELECT * FROM df")
            con.unregister("df")
            con.execute("INSERT OR REPLACE INTO fo_loaded VALUES (?, ?, ?)", [d, len(df), datetime.now(timezone.utc)])
            out["days"] += 1
            out["rows"] += len(df)
    finally:
        con.close()
    return out


def load_small(con, cfg, start: date, end: date) -> dict:
    """Ban list and participant OI / volume into the research database (small tables)."""
    lake = RawLake(cfg.lake)
    now = datetime.now(timezone.utc)
    out = {"fo_ban": 0, "participant_oi": 0, "participant_vol": 0, "failed": 0}
    for d, key in _objects(lake, "fo_ban", start, end):
        syms = parse_ban(lake.read(key))
        con.execute("DELETE FROM fo_ban WHERE trade_date = ?", [d])
        con.executemany("INSERT INTO fo_ban VALUES (?, ?, ?)", [[d, s, now] for s in syms])
        con.execute("INSERT OR REPLACE INTO fo_ban_days VALUES (?, ?, ?)", [d, len(syms), now])
        out["fo_ban"] += 1
    for kind in ("participant_oi", "participant_vol"):
        for d, key in _objects(lake, kind, start, end):
            try:
                df = parse_participant(lake.read(key), d)
            except Exception:                           # noqa: BLE001
                out["failed"] += 1
                continue
            df["kind"] = "oi" if kind == "participant_oi" else "volume"
            df["observed_at"] = now
            con.register("df", df[["trade_date", "kind", "client_type", "measure", "contracts", "observed_at"]])
            con.execute("INSERT OR REPLACE INTO participant_positions SELECT * FROM df")
            con.unregister("df")
            out[kind] += 1
    return out
