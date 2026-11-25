"""Re-score every experiment whose net series or half-sample t's were kept, under ADR-0007's dynamic standard.

This spends no ADR-0004 trial: it reads results already recorded. It is post hoc by construction (the prior
classes and family sizes below were assigned on 2026-11-16, after the results were known), so its output is a
ranking of where forward evidence should be collected, not a set of new verdicts.

    python scripts/research/rescore_dynamic.py            -> docs/trials/dynamic/rescore-2026-11-16.{json,md}
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from financial_brain.evaluation import dynamic_standard as D

EDGES = Path("data/edges")
OUT = Path("docs/trials/dynamic")

# Family sizes: variants of the same idea examined in this project (the count that Sidak-adjusts the p-value).
MOM = 20        # P0-P3, E1, W2, W3, M1, A1 plus the horizon sweep's momentum columns
VOL = 8         # vol_60, atr, ivol, MAX, BAB, low-vol book, vol-of-vol, downside beta
PEAD = 6        # N1, N2, S2, S2x, X3, EAP
CARRY = 4       # the four basis thresholds
SEASON = 8      # turn of month, pre-holiday, day of week, Halloween, year end, return seasonality, overnight, I2
REV = 6         # W1, S1, S1x, long-term reversal, opening fade, index mean reversion
OI = 4
VOLUME = 6      # A10-A13, HV1, HV2, delivery
TECH_DAILY = 17 + 9
INTRADAY = 9 + 31 + 42

#: (file, column, name, prior, family, direction, kind, null_pass)
#: kind: "book" = a long or hedged book that can hold capital; "avoid" = the measured side loses, usable as a filter.
SERIES = [
    ("m1_net", "net", "M1 cost-aware momentum book (40 sessions)", "A", MOM, 1, "book", True),
    ("P0", "net", "P0 plain 12-1 momentum, top 20, monthly", "A", MOM, 1, "book", None),
    ("P1", "net", "P1 residual momentum", "A", MOM, 1, "book", None),
    ("P2", "net", "P2 volatility-managed momentum", "A", MOM, 1, "book", None),
    ("P3", "net", "P3 frog-in-the-pan momentum", "A", MOM, 1, "book", None),
    ("E1", "net", "E1 momentum ensemble", "A", MOM, 1, "book", None),
    ("W2", "net", "W2 52-week-high long-short in futures", "A", MOM, 1, "book", None),
    ("W3", "net", "W3 momentum long-short in futures", "A", MOM, 1, "book", None),
    ("a10_series", "A1_intermediate_mom", "A1 intermediate momentum", "A", MOM, 1, "book", True),
    ("c1_monthly", "net", "C1 rich-basis carry (>10%)", "B", CARRY, 1, "book", True),
    ("N2", "ex", "N2 bottom-fifth results reaction (avoid/short)", "A", PEAD, -1, "avoid", None),
    ("N1", "ex", "N1 weekly news losers (avoid/short)", "B", PEAD, -1, "avoid", None),
    ("S2", "net", "S2 results reaction drift, long", "A", PEAD, 1, "book", None),
    ("S2x", "net", "S2x reaction drift variant", "A", PEAD, 1, "book", None),
    ("eapr_monthly", "net", "EAPR short into scheduled results (chosen after EAP failed)", "C", PEAD, 1, "book", None),
    ("S1", "net", "S1 no-news reversal", "B", REV, 1, "book", None),
    ("S1x", "net", "S1x reversal variant", "B", REV, 1, "book", None),
    ("W1", "net", "W1 reversal long-short in futures", "B", REV, 1, "book", None),
    ("X2", "net", "X2 opening fade", "C", REV, 1, "book", None),
    ("a10_series", "A2_long_term_reversal", "A2 long-term reversal", "B", REV, 1, "book", False),
    ("Z1", "net", "Z1 turn of month (Nifty)", "B", SEASON, 1, "book", None),
    ("Z2", "net", "Z2 pre-holiday (Nifty)", "B", SEASON, 1, "book", None),
    ("a10_series", "A3_return_seasonality", "A3 return seasonality", "B", SEASON, 1, "book", False),
    ("a10_series", "A14_day_of_week", "A14 day of week", "C", SEASON, 1, "book", None),
    ("a10_series", "A15_halloween", "A15 Halloween", "C", SEASON, 1, "book", None),
    ("bab_monthly", "net", "BAB betting against beta in futures", "A", VOL, 1, "book", False),
    ("a10_series", "A4_ivol", "A4 low idiosyncratic volatility, long", "A", VOL, 1, "book", False),
    ("a10_series", "A5_max", "A5 low MAX, long", "B", VOL, 1, "book", False),
    ("a10_series", "A6_idio_skew", "A6 low idiosyncratic skew, long", "B", VOL, 1, "book", False),
    ("a10_series", "A7_vol_of_vol", "A7 vol of vol", "C", VOL, 1, "book", False),
    ("a10_series", "A8_downside_beta", "A8 downside beta", "B", VOL, 1, "book", False),
    ("a10_series", "A9_coskewness", "A9 coskewness", "B", VOL, 1, "book", False),
    ("a10_series", "A10_amihud", "A10 Amihud illiquidity, long", "B", VOLUME, 1, "book", False),
    ("a10_series", "A11_volume_trend", "A11 falling volume, long (as registered)", "C", VOLUME, 1, "book", False),
    ("a10_series", "A11_volume_trend", "A11 falling volume as an AVOID filter", "C", VOLUME, -1, "avoid", None),
    ("a10_series", "A12_zero_days", "A12 zero-return days", "C", VOLUME, 1, "book", False),
    ("a10_series", "A13_illiq_shock", "A13 worsening liquidity, long (as registered)", "C", VOLUME, 1, "book", False),
    ("a10_series", "A13_illiq_shock", "A13 worsening liquidity as an AVOID filter", "C", VOLUME, -1, "avoid", None),
    ("a10_series", "A6_idio_skew", "A6 low idiosyncratic skew as an AVOID filter", "C", VOL, -1, "avoid", None),
    ("hv1_rising_volume_net", "net", "HV1 rising volume, long", "B", VOLUME, 1, "book", None),
    ("hv2_improving_liquidity_net", "net", "HV2 improving liquidity, long", "B", VOLUME, 1, "book", None),
    ("d1_net", "net", "D1 delivery share long-short", "C", VOLUME, 1, "book", False),
    ("F1", "net", "F1 long build-up", "C", OI, 1, "book", None),
    ("F2", "net", "F2 short covering", "C", OI, 1, "book", None),
    ("f3_short_net", "net", "F3 short build-up, short side", "C", OI, 1, "book", False),
    ("L2", "net", "L2 learned long-short in futures", "C", 5, 1, "book", None),
]

#: (result json, family size, prior, label) for families kept only as half-sample t's.
HALVES = [("k7_result", TECH_DAILY, "C", "K7 chart/candle"), ("b4_result", TECH_DAILY, "C", "B4 index rule"),
          ("t11_result", INTRADAY, "C", "I/S minute-bar")]


def _load(file: str, col: str) -> pd.Series:
    return pd.read_parquet(EDGES / f"{file}.parquet")[col].dropna()


def rows() -> list[dict]:
    out = []
    for file, col, name, prior, fam, direction, kind, null in SERIES:
        r = D.judge(D.Spec(name, prior, fam, direction), _load(file, col), null_pass=null)
        r["kind"] = kind
        if kind == "avoid":
            r["capital_fraction"] = 0.0
        out.append(r)
    for file, fam, prior, label in HALVES:
        res = json.loads((EDGES / f"{file}.json").read_text())
        for key, v in res.items():
            g3 = v.get("g3") if isinstance(v, dict) else None
            if not g3:
                continue
            r = D.judge_from_halves(D.Spec(f"{label}: {key}", prior, fam), g3["t_a"], g3["n_a"], g3["t_b"], g3["n_b"],
                                    null_pass=(v.get("g4") or {}).get("pass"))
            r["kind"] = "book"
            out.append(r)
    v2 = json.loads((EDGES / "v2_result.json").read_text())
    for key, v in v2.get("rules", {}).items():
        t = v.get("t")
        if not t or len(t) != 2:
            continue
        n = int(v.get("months", 120))
        r = D.judge_from_halves(D.Spec(f"V2 intraday: {key}", "C", INTRADAY), t[0], n // 2, t[1], n - n // 2,
                                null_pass=None)
        r["kind"] = "book"
        out.append(r)
    return out


def table(rs: list[dict]) -> str:
    order = {"CANDIDATE": 0, "CANDIDATE_SMALL": 1, "INCUBATE": 2, "FAIL": 3}
    rs = sorted(rs, key=lambda r: (order[r["tier"]], -r["posterior"]))
    lines = ["| experiment | kind | prior | family | t used | needs (small / candidate) | halves t | P(real) | tier | "
             "shrunk Sharpe | capital |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rs:
        lines.append(f"| {r['name']} | {r['kind']} | {r['prior']} | {r['family']} | {r['t_used']:+.2f} | "
                     f"{r['need_small']:.2f} / {r['need_candidate']:.2f} | {r['t_a']:+.2f} / {r['t_b']:+.2f} | "
                     f"{r['posterior']:.2f} | {r['tier']} | {r.get('sharpe_shrunk', '')} | "
                     f"{r.get('capital_fraction', '')} |")
    return "\n".join(lines)


def main() -> None:
    rs = rows()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "rescore-2026-11-16.json").write_text(json.dumps(rs, indent=1, default=str), encoding="utf-8")
    counts = pd.Series([r["tier"] for r in rs]).value_counts().to_dict()
    head = (f"# Past experiments under the dynamic standard (ADR-0007)\n\nPost hoc: priors and family sizes were "
            f"assigned on 2026-11-16 with the results known. {len(rs)} experiments: {counts}.\n\n")
    (OUT / "rescore-2026-11-16.md").write_text(head + table(rs) + "\n", encoding="utf-8")
    print(counts)
    keep = [r for r in rs if r["tier"] != "FAIL"]
    print(pd.DataFrame(keep)[["name", "kind", "prior", "family", "t_iid", "t_hac", "t_used", "z_adj", "t_a", "t_b",
                              "posterior", "tier", "sharpe", "sharpe_shrunk", "capital_fraction"]].to_string())
    near = sorted([r for r in rs if r["tier"] == "FAIL" and r["both_halves"]], key=lambda r: -r["posterior"])[:12]
    print(pd.DataFrame(near)[["name", "prior", "family", "t_used", "need_small", "posterior"]].to_string())


if __name__ == "__main__":
    main()
