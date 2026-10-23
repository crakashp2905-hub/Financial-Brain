"""Monthly business updates found among the announcements by their own subject line.

Auto makers file monthly sales, cement makers despatches, power producers generation, lenders and AMCs their loan
book or AUM - weeks before the quarter's results. This indexes those filings (``monthly_updates``) with a kind; the
figures stay in the attachment, to be read by the document extractors when a registered test needs them. Rules only.
"""
from __future__ import annotations

import re

KINDS = [
    ("auto_sales", re.compile(r"\b(auto(mobile)?|vehicle|tractor|two[- ]wheeler|car)s?\b.{0,40}\bsales\b|"
                              r"\bsales\b.{0,30}\b(units|volumes?)\b|monthly sales|\bsales\b.{0,20}\bfor the month\b",
                              re.I)),
    ("cement_steel_volumes", re.compile(r"\b(despatch|dispatch|cement|steel)(es)?\b.{0,40}\b(volumes?|production|sales)\b", re.I)),
    ("power_generation", re.compile(r"\b(power|energy)\s+generation\b|\bgeneration\b.{0,20}\b(mu|million units)\b", re.I)),
    ("business_update", re.compile(r"\b(business|operational|monthly)\s+update\b|\bprovisional\b.{0,40}\b(business|"
                                   r"figures|numbers)\b|\b(aum|loan book|disbursements?|deposits?)\b.{0,30}\b(month|as on)\b", re.I)),
    ("production", re.compile(r"\bmonthly\b.{0,30}\bproduction\b|\bproduction\b.{0,20}\bfor the month\b", re.I)),
]


def kind_of(text: str) -> str | None:
    for name, rx in KINDS:
        if rx.search(text or ""):
            return name
    return None


def rebuild(con) -> dict:
    import collections

    import pandas as pd
    rows = con.execute("""SELECT news_id, isin, company, business_date, published_at,
                                 coalesce(headline, '') || ' ' || coalesce(subject, '') AS t, attachment
                          FROM announcements
                          WHERE regexp_matches(lower(coalesce(headline, '') || ' ' || coalesce(subject, '')),
                                               'sales|despatch|dispatch|generation|update|production|aum|loan book|disburse')"""
                       ).fetchall()
    keep = [(n, i, c, d, p, kind_of(t), t[:400], a) for n, i, c, d, p, t, a in rows if kind_of(t)]
    df = pd.DataFrame(keep, columns=["news_id", "isin", "company", "business_date", "published_at", "kind", "headline",
                                     "attachment"]).drop_duplicates("news_id")
    con.execute("DELETE FROM monthly_updates")
    if len(df):
        con.register("df", df)
        con.execute("INSERT INTO monthly_updates SELECT * FROM df")
        con.unregister("df")
    return {"rows": len(df), "kinds": dict(collections.Counter(df["kind"])) if len(df) else {}}
