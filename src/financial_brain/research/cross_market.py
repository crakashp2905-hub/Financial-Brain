"""As-of US-to-India domain exposure alignment for research, never trade instructions.

US observations must carry an actual publication/market-close timestamp in UTC. A
mapping is an explicit, dated economic hypothesis, not a discovered correlation.
"""
from __future__ import annotations

import pandas as pd

REQUIRED_US = {"proxy", "available_at", "return"}
REQUIRED_INDIA = {"ticker", "decision_at"}
REQUIRED_MAP = {"ticker", "domain", "proxy", "weight", "valid_from", "valid_to"}


def align(us: pd.DataFrame, india: pd.DataFrame, mapping: pd.DataFrame,
          *, max_age_hours: int = 96) -> pd.DataFrame:
    """Latest *already available* US proxy return per dated Indian company exposure.

    The caller supplies decision timestamps (e.g. 09:00 IST) and manually reviewed
    domain weights. No same-day US close is allowed if it occurs after that decision.
    """
    for name, frame, required in (("US", us, REQUIRED_US), ("India", india, REQUIRED_INDIA),
                                  ("mapping", mapping, REQUIRED_MAP)):
        missing = required - set(frame)
        if missing:
            raise ValueError(f"{name} missing fields: {sorted(missing)}")
    if max_age_hours <= 0:
        raise ValueError("max_age_hours must be positive")
    u, i, m = us.copy(), india.copy(), mapping.copy()
    u["available_at"] = pd.to_datetime(u["available_at"], utc=True)
    i["decision_at"] = pd.to_datetime(i["decision_at"], utc=True)
    m["valid_from"] = pd.to_datetime(m["valid_from"], utc=True)
    m["valid_to"] = pd.to_datetime(m["valid_to"], utc=True)
    if (u["return"].isna().any() or not pd.api.types.is_numeric_dtype(u["return"])
            or m["weight"].isna().any() or not pd.api.types.is_numeric_dtype(m["weight"])):
        raise ValueError("returns and mapping weights must be numeric and present")
    if ((m["weight"] < 0) | (m["weight"] > 1)).any():
        raise ValueError("domain weights must be in [0, 1]")
    if (m["valid_to"] < m["valid_from"]).any():
        raise ValueError("mapping validity interval is reversed")
    expanded = i.merge(m, on="ticker", how="inner")
    expanded = expanded[(expanded["decision_at"] >= expanded["valid_from"])
                        & (expanded["decision_at"] <= expanded["valid_to"])]
    if expanded.empty:
        return pd.DataFrame(columns=["ticker", "decision_at", "domain", "proxy",
                                     "weight", "us_return", "us_available_at", "age_hours"])
    out = pd.merge_asof(expanded.sort_values("decision_at"),
                        u.rename(columns={"return": "us_return", "available_at": "us_available_at"})
                         .sort_values("us_available_at"),
                        left_on="decision_at", right_on="us_available_at", by="proxy",
                        direction="backward", allow_exact_matches=False,
                        tolerance=pd.Timedelta(hours=max_age_hours))
    out["age_hours"] = (out["decision_at"] - out["us_available_at"]).dt.total_seconds() / 3600
    return out[["ticker", "decision_at", "domain", "proxy", "weight",
                "us_return", "us_available_at", "age_hours"]].reset_index(drop=True)


def correlation_diagnostic(aligned: pd.DataFrame, outcomes: pd.DataFrame,
                           *, min_pairs: int = 30) -> pd.DataFrame:
    """Descriptive same-hypothesis correlation; no multiplicity or causal claim."""
    if min_pairs < 20:
        raise ValueError("min_pairs must be at least 20")
    if not {"ticker", "decision_at", "next_return"}.issubset(outcomes):
        raise ValueError("outcomes require ticker, decision_at, next_return")
    o = outcomes.copy()
    o["decision_at"] = pd.to_datetime(o["decision_at"], utc=True)
    joined = aligned.merge(o, on=["ticker", "decision_at"], validate="many_to_one")
    joined = joined.dropna(subset=["us_return", "next_return"])
    rows = []
    for (domain, proxy), group in joined.groupby(["domain", "proxy"]):
        n = len(group)
        rows.append({"domain": domain, "proxy": proxy, "pairs": n,
                     "correlation": float(group["us_return"].corr(group["next_return"]))
                     if n >= min_pairs and group["us_return"].nunique() > 1
                     and group["next_return"].nunique() > 1 else None,
                     "status": "DESCRIPTIVE_ONLY" if n >= min_pairs else "INSUFFICIENT_PAIRS"})
    return pd.DataFrame(rows, columns=["domain", "proxy", "pairs", "correlation", "status"])
