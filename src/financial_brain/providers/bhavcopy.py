"""NSE and BSE daily bhavcopy providers (UDiFF format).

Both exchanges publish the same UDiFF (Unified Data Interchange File Format) schema and
- importantly - **both carry ISIN natively**. That makes ISIN-keyed identity
(docs/ARCHITECTURE.md "Canonical security identity") a parse away rather than a
reconciliation project.

Verified columns (2026-09-11, both exchanges, 34 fields):

    TradDt, BizDt, Sgmt, Src, FinInstrmTp, FinInstrmId, ISIN, TckrSymb, SctySrs,
    XpryDt, FininstrmActlXpryDt, StrkPric, OptnTp, FinInstrmNm, OpnPric, HghPric,
    LwPric, ClsPric, LastPric, PrvsClsgPric, UndrlygPric, SttlmPric, OpnIntrst,
    ChngInOpnIntrst, TtlTradgVol, TtlTrfVal, TtlNbOfTxsExctd, SsnId, NewBrdLotQty,
    Rmks, Rsvd1..Rsvd4

``FinInstrmId`` is the exchange's own instrument id - the BSE scrip code on BSE, the
token on NSE - so we keep it as a secondary identifier.

Data rights: these archives are published for public download. Redistribution or
commercial use of exchange data requires an agreement with the exchange. See
docs/FEASIBILITY-INDIA.md and the Data rights note in the vault.
"""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, datetime

from .base import FetchError, FetchResult, NotPublished, Provider

#: Both exchanges moved to UDiFF in July 2024. Before that each published its own
#: format. Rather than fork the pipeline, the legacy formats are *normalised into UDiFF*
#: by ``to_csv`` - so the ingest job, quality contracts, security master and universe
#: code are identical for a 2015 file and a 2026 one.
UDIFF_CUTOVER = date(2024, 7, 8)

UDIFF_COLUMNS = [
    "TradDt", "BizDt", "Sgmt", "Src", "FinInstrmTp", "FinInstrmId", "ISIN", "TckrSymb",
    "SctySrs", "XpryDt", "FininstrmActlXpryDt", "StrkPric", "OptnTp", "FinInstrmNm",
    "OpnPric", "HghPric", "LwPric", "ClsPric", "LastPric", "PrvsClsgPric", "UndrlygPric",
    "SttlmPric", "OpnIntrst", "ChngInOpnIntrst", "TtlTradgVol", "TtlTrfVal",
    "TtlNbOfTxsExctd", "SsnId", "NewBrdLotQty", "Rmks", "Rsvd1", "Rsvd2", "Rsvd3", "Rsvd4",
]


def _udiff_line(**vals) -> str:
    return ",".join(str(vals.get(col, "")) for col in UDIFF_COLUMNS)


def _parse_legacy_date(raw: str) -> str:
    """Legacy files date rows as '01-JAN-2020' (NSE) or '03-Jan-23' (BSE)."""
    raw = (raw or "").strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d-%b-%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return raw


def _sniff(text: str) -> str:
    head = text[:200]
    if "TradDt" in head:
        return "udiff"
    if "SYMBOL" in head and "SERIES" in head:
        return "nse_legacy"
    if "SC_CODE" in head:
        return "bse_legacy" if "ISIN_CODE" in head else "bse_pre_isin"
    return "unknown"


def _normalise_nse_legacy(text: str) -> bytes:
    """SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,
    TIMESTAMP,TOTALTRADES,ISIN -> UDiFF."""
    out = [",".join(UDIFF_COLUMNS)]
    for r in csv.DictReader(io.StringIO(text)):
        row = {(k or "").strip(): (v or "").strip() for k, v in r.items() if k}
        isin = row.get("ISIN", "")
        if not isin:
            continue
        d = _parse_legacy_date(row.get("TIMESTAMP", ""))
        out.append(_udiff_line(
            TradDt=d, BizDt=d, Sgmt="CM", Src="NSE", FinInstrmTp="STK",
            FinInstrmId="", ISIN=isin, TckrSymb=row.get("SYMBOL", ""),
            SctySrs=row.get("SERIES", ""), FinInstrmNm=row.get("SYMBOL", ""),
            OpnPric=row.get("OPEN", ""), HghPric=row.get("HIGH", ""),
            LwPric=row.get("LOW", ""), ClsPric=row.get("CLOSE", ""),
            LastPric=row.get("LAST", ""), PrvsClsgPric=row.get("PREVCLOSE", ""),
            SttlmPric=row.get("CLOSE", ""), TtlTradgVol=row.get("TOTTRDQTY", ""),
            TtlTrfVal=row.get("TOTTRDVAL", ""), TtlNbOfTxsExctd=row.get("TOTALTRADES", ""),
            SsnId="F1", NewBrdLotQty="1"))
    return ("\n".join(out) + "\n").encode()


def _normalise_bse_legacy(text: str) -> bytes:
    """SC_CODE,SC_NAME,SC_GROUP,SC_TYPE,OPEN,...,ISIN_CODE,TRADING_DATE -> UDiFF."""
    out = [",".join(UDIFF_COLUMNS)]
    for r in csv.DictReader(io.StringIO(text)):
        row = {(k or "").strip(): (v or "").strip() for k, v in r.items() if k}
        isin = row.get("ISIN_CODE", "")
        if not isin:
            continue
        d = _parse_legacy_date(row.get("TRADING_DATE", ""))
        out.append(_udiff_line(
            TradDt=d, BizDt=d, Sgmt="CM", Src="BSE", FinInstrmTp="STK",
            FinInstrmId=row.get("SC_CODE", ""), ISIN=isin,
            TckrSymb=row.get("SC_NAME", "").strip(),
            SctySrs=row.get("SC_GROUP", "").strip(),
            FinInstrmNm=row.get("SC_NAME", "").strip(),
            OpnPric=row.get("OPEN", ""), HghPric=row.get("HIGH", ""),
            LwPric=row.get("LOW", ""), ClsPric=row.get("CLOSE", ""),
            LastPric=row.get("LAST", ""), PrvsClsgPric=row.get("PREVCLOSE", ""),
            SttlmPric=row.get("CLOSE", ""), TtlTradgVol=row.get("NO_OF_SHRS", ""),
            TtlTrfVal=row.get("NET_TURNOV", ""), TtlNbOfTxsExctd=row.get("NO_TRADES", ""),
            SsnId="F1", NewBrdLotQty="1"))
    return ("\n".join(out) + "\n").encode()


def _normalise_bse_pre_isin(text: str, inner_name: str) -> bytes:
    """BSE before 8 Dec 2016: EQddmmyy.CSV - no ISIN and no date column.

    The date comes from the exchange's own inner filename. ISIN is left blank here:
    it cannot be derived from the payload alone, and guessing it from the scrip code
    is unsafe because a code survives the ISIN change a face-value split causes
    (884 BSE codes have carried more than one ISIN). Resolution happens in the ingest
    job, against the database, with same-day NSE corroboration - see
    ``ingest/bse_isin.py``.
    """
    stem = inner_name.upper().rsplit("/", 1)[-1]
    try:
        d = datetime.strptime(stem[2:8], "%d%m%y").date().isoformat()
    except ValueError as e:
        raise FetchError(f"cannot read a date from BSE file name {inner_name!r}",
                         retryable=False) from e
    out = [",".join(UDIFF_COLUMNS)]
    for r in csv.DictReader(io.StringIO(text)):
        row = {(k or "").strip(): (v or "").strip() for k, v in r.items() if k}
        if not row.get("SC_CODE"):
            continue
        out.append(_udiff_line(
            TradDt=d, BizDt=d, Sgmt="CM", Src="BSE", FinInstrmTp="STK",
            FinInstrmId=row.get("SC_CODE", ""), ISIN="",
            TckrSymb=row.get("SC_NAME", ""), SctySrs=row.get("SC_GROUP", ""),
            FinInstrmNm=row.get("SC_NAME", ""),
            OpnPric=row.get("OPEN", ""), HghPric=row.get("HIGH", ""),
            LwPric=row.get("LOW", ""), ClsPric=row.get("CLOSE", ""),
            LastPric=row.get("LAST", ""), PrvsClsgPric=row.get("PREVCLOSE", ""),
            SttlmPric=row.get("CLOSE", ""), TtlTradgVol=row.get("NO_OF_SHRS", ""),
            TtlTrfVal=row.get("NET_TURNOV", ""), TtlNbOfTxsExctd=row.get("NO_TRADES", ""),
            SsnId="F1", NewBrdLotQty="1"))
    return ("\n".join(out) + "\n").encode()


def normalise(payload: bytes) -> bytes:
    """Return UDiFF CSV bytes for any supported bhavcopy payload, zipped or not.

    Format is sniffed from the content rather than inferred from the date, so a
    mislabelled or re-dated file cannot be parsed with the wrong reader.
    """
    inner_name = ""
    if payload[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            inner_name = zf.namelist()[0]
            payload = zf.read(inner_name)
    text = payload.decode("utf-8", errors="replace")
    kind = _sniff(text)
    if kind == "udiff":
        return payload
    if kind == "nse_legacy":
        return _normalise_nse_legacy(text)
    if kind == "bse_legacy":
        return _normalise_bse_legacy(text)
    if kind == "bse_pre_isin":
        return _normalise_bse_pre_isin(text, inner_name)
    raise FetchError(f"unrecognised bhavcopy format (head: {text[:60]!r})", retryable=False)


class NSEBhavcopyProvider(Provider):
    """NSE cash-market bhavcopy, UDiFF, delivered as a zip containing one CSV."""

    source = "NSE"
    dataset = "bhavcopy_cm"
    tier = 1

    URL = (
        "https://nsearchives.nseindia.com/content/cm/"
        "BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
    )
    #: Pre-July-2024 archive. Verified reachable back to at least 2000-01-03.
    LEGACY_URL = (
        "https://nsearchives.nseindia.com/content/historical/EQUITIES/"
        "{yyyy}/{mon}/cm{dd}{mon}{yyyy}bhav.csv.zip"
    )

    def _legacy_url(self, business_date: date) -> str:
        return self.LEGACY_URL.format(
            yyyy=business_date.strftime("%Y"), mon=business_date.strftime("%b").upper(),
            dd=business_date.strftime("%d"))

    def fetch(self, business_date: date) -> FetchResult:
        legacy = business_date < UDIFF_CUTOVER
        url = self._legacy_url(business_date) if legacy else self.URL.format(
            yyyymmdd=business_date.strftime("%Y%m%d"))
        headers = {"Referer": "https://www.nseindia.com/"}
        try:
            payload, status, ctype = self._http_get(url, headers=headers)
        except FetchError as e:
            if e.status in (403, 404):
                raise NotPublished(f"NSE has no bhavcopy for {business_date} ({e.status})") from e
            raise

        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                inner = zf.namelist()[0]
        except zipfile.BadZipFile as e:
            raise FetchError(f"NSE returned a non-zip payload for {business_date}",
                             retryable=False) from e

        return FetchResult(
            payload=payload,
            url=url,
            retrieved_at=self._now(),
            filename=(f"cm_NSE_{business_date:%Y%m%d}_legacy.csv.zip" if legacy
                      else f"BhavCopy_NSE_CM_{business_date:%Y%m%d}.csv.zip"),
            content_type=ctype,
            http_status=status,
            extra={"inner_csv": inner, "format": "legacy" if legacy else "udiff"},
        )

    @staticmethod
    def to_csv(payload: bytes) -> bytes:
        return normalise(payload)


class BSEBhavcopyProvider(Provider):
    """BSE cash-market bhavcopy, UDiFF, delivered as a plain CSV."""

    source = "BSE"
    dataset = "bhavcopy_cm"
    tier = 1

    URL = (
        "https://www.bseindia.com/download/BhavCopy/Equity/"
        "BhavCopy_BSE_CM_0_0_0_{yyyymmdd}_F_0000.CSV"
    )
    #: Pre-July-2024 archive, zipped, dated ddmmyy.
    #: Pre-8-Dec-2016 archive: no ISIN, no date column.
    PRE_ISIN_URL = ("https://www.bseindia.com/download/BhavCopy/Equity/"
                    "EQ{ddmmyy}_CSV.ZIP")
    LEGACY_URL = ("https://www.bseindia.com/download/BhavCopy/Equity/"
                  "EQ_ISINCODE_{ddmmyy}.zip")

    def fetch(self, business_date: date) -> FetchResult:
        if business_date < UDIFF_CUTOVER:
            return self._fetch_legacy(business_date)
        url = self.URL.format(yyyymmdd=business_date.strftime("%Y%m%d"))
        try:
            payload, status, ctype = self._http_get(url, headers={"Referer": "https://www.bseindia.com/"})
        except FetchError as e:
            if e.status in (403, 404):
                raise NotPublished(f"BSE has no bhavcopy for {business_date} ({e.status})") from e
            raise

        head = payload[:200].decode("utf-8", errors="replace")
        if "TradDt" not in head:
            # BSE serves its SPA landing page instead of a 404 when a file does not
            # exist, so an HTML body means "no data for this date" - a holiday, not a
            # failure. Confirmed against 2026-06-26 and 2026-09-14, both of which NSE
            # also reported as non-trading days.
            if "<!DOCTYPE html" in head or "<html" in head.lower():
                raise NotPublished(f"BSE has no bhavcopy for {business_date} (HTML landing page)")
            raise FetchError(
                f"BSE payload for {business_date} is not a UDiFF CSV (got: {head[:60]!r})",
                retryable=False,
            )

        return FetchResult(
            payload=payload,
            url=url,
            retrieved_at=self._now(),
            filename=f"BhavCopy_BSE_CM_{business_date:%Y%m%d}.csv",
            content_type=ctype,
            http_status=status,
        )

    def _fetch_legacy(self, business_date: date) -> FetchResult:
        url = self.LEGACY_URL.format(ddmmyy=business_date.strftime("%d%m%y"))
        try:
            payload, status, ctype = self._http_get(
                url, headers={"Referer": "https://www.bseindia.com/"})
        except FetchError as e:
            if e.status in (403, 404):
                raise NotPublished(
                    f"BSE has no legacy bhavcopy for {business_date}") from e
            raise
        if payload[:2] != b"PK":
            # Before 8 Dec 2016 BSE published only EQddmmyy_CSV.ZIP (no ISIN).
            url = self.PRE_ISIN_URL.format(ddmmyy=business_date.strftime("%d%m%y"))
            try:
                payload, status, ctype = self._http_get(
                    url, headers={"Referer": "https://www.bseindia.com/"})
            except FetchError as e:
                if e.status in (403, 404):
                    raise NotPublished(f"BSE has no bhavcopy for {business_date}") from e
                raise
            if payload[:2] != b"PK":
                raise NotPublished(f"BSE has no bhavcopy for {business_date} (not a zip)")
            return FetchResult(
                payload=payload, url=url, retrieved_at=self._now(),
                filename=f"EQ_{business_date:%Y%m%d}_pre_isin.zip",
                content_type=ctype, http_status=status, extra={"format": "pre_isin"})
        return FetchResult(
            payload=payload, url=url, retrieved_at=self._now(),
            filename=f"EQ_ISINCODE_{business_date:%Y%m%d}.zip",
            content_type=ctype, http_status=status, extra={"format": "legacy"})

    @staticmethod
    def to_csv(payload: bytes) -> bytes:
        return normalise(payload)


PROVIDERS = {
    "NSE": NSEBhavcopyProvider,
    "BSE": BSEBhavcopyProvider,
}
