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

import io
import zipfile
from datetime import date

from .base import FetchError, FetchResult, NotPublished, Provider

UDIFF_COLUMNS = [
    "TradDt", "BizDt", "Sgmt", "Src", "FinInstrmTp", "FinInstrmId", "ISIN", "TckrSymb",
    "SctySrs", "XpryDt", "FininstrmActlXpryDt", "StrkPric", "OptnTp", "FinInstrmNm",
    "OpnPric", "HghPric", "LwPric", "ClsPric", "LastPric", "PrvsClsgPric", "UndrlygPric",
    "SttlmPric", "OpnIntrst", "ChngInOpnIntrst", "TtlTradgVol", "TtlTrfVal",
    "TtlNbOfTxsExctd", "SsnId", "NewBrdLotQty", "Rmks", "Rsvd1", "Rsvd2", "Rsvd3", "Rsvd4",
]


class NSEBhavcopyProvider(Provider):
    """NSE cash-market bhavcopy, UDiFF, delivered as a zip containing one CSV."""

    source = "NSE"
    dataset = "bhavcopy_cm"
    tier = 1

    URL = (
        "https://nsearchives.nseindia.com/content/cm/"
        "BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
    )

    def fetch(self, business_date: date) -> FetchResult:
        url = self.URL.format(yyyymmdd=business_date.strftime("%Y%m%d"))
        try:
            payload, status, ctype = self._http_get(url, headers={"Referer": "https://www.nseindia.com/"})
        except FetchError as e:
            if e.status in (403, 404):
                raise NotPublished(f"NSE has no bhavcopy for {business_date} ({e.status})") from e
            raise

        # Unzip here so the curated layer sees CSV, but keep the *zip* in the lake:
        # the lake stores exactly what the source served us.
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                inner = zf.namelist()[0]
        except zipfile.BadZipFile as e:
            raise FetchError(f"NSE returned a non-zip payload for {business_date}", retryable=False) from e

        return FetchResult(
            payload=payload,
            url=url,
            retrieved_at=self._now(),
            filename=f"BhavCopy_NSE_CM_{business_date:%Y%m%d}.csv.zip",
            content_type=ctype,
            http_status=status,
            extra={"inner_csv": inner},
        )

    @staticmethod
    def to_csv(payload: bytes) -> bytes:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            return zf.read(zf.namelist()[0])


class BSEBhavcopyProvider(Provider):
    """BSE cash-market bhavcopy, UDiFF, delivered as a plain CSV."""

    source = "BSE"
    dataset = "bhavcopy_cm"
    tier = 1

    URL = (
        "https://www.bseindia.com/download/BhavCopy/Equity/"
        "BhavCopy_BSE_CM_0_0_0_{yyyymmdd}_F_0000.CSV"
    )

    def fetch(self, business_date: date) -> FetchResult:
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

    @staticmethod
    def to_csv(payload: bytes) -> bytes:
        return payload


PROVIDERS = {
    "NSE": NSEBhavcopyProvider,
    "BSE": BSEBhavcopyProvider,
}
