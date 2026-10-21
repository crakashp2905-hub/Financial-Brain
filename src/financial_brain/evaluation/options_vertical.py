"""Observed-close, defined-risk NIFTY vertical-spread diagnostics.

No bid/ask archive, PIT lot sizes, margin, complete taxes or exercise fees are
available here. These are training-only *price proxies*, never live trade signals.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def load_wings(con, atm: pd.DataFrame, *, min_contracts: int = 100) -> pd.DataFrame:
    """Select an approximately 1%-OTM same-expiry wing at signal close.

    Future wing quotes are looked up only after the wing has been selected. Missing
    future prices stay missing so the diagnostic can show the coverage loss.
    """
    if atm.empty:
        return pd.DataFrame()
    if min_contracts < 1:
        raise ValueError("min_contracts must be positive")
    con.register("_atm_candidates", atm)
    try:
        return con.execute("""
        WITH ranked AS (
            SELECT b.*, o.strike AS wing_strike,
                   ROW_NUMBER() OVER (
                       PARTITION BY b.signal_date, b.option_type
                       ORDER BY ABS(o.strike - CASE WHEN b.option_type = 'CE'
                                             THEN b.strike * 1.01 ELSE b.strike * 0.99 END),
                                o.strike
                   ) AS wing_rank
            FROM _atm_candidates b JOIN fo_bhav o
              ON o.trade_date = b.signal_date AND o.instrument = 'OPTIDX'
             AND o.symbol = 'NIFTY' AND o.expiry = b.expiry
             AND o.option_type = b.option_type
            WHERE o.contracts >= ? AND o.close > 0
              AND ((b.option_type = 'CE' AND o.strike > b.strike)
                OR (b.option_type = 'PE' AND o.strike < b.strike))
              AND ABS(o.strike / b.strike - 1) BETWEEN 0.005 AND 0.03
        )
        SELECT r.* EXCLUDE (wing_rank), e.close AS wing_entry_close,
               x.close AS wing_exit_close, e.contracts AS wing_entry_contracts,
               x.contracts AS wing_exit_contracts
        FROM ranked r
        LEFT JOIN fo_bhav e ON e.trade_date = r.entry_date AND e.instrument = 'OPTIDX'
             AND e.symbol = 'NIFTY' AND e.expiry = r.expiry
             AND e.option_type = r.option_type AND e.strike = r.wing_strike
        LEFT JOIN fo_bhav x ON x.trade_date = r.exit_date AND x.instrument = 'OPTIDX'
             AND x.symbol = 'NIFTY' AND x.expiry = r.expiry
             AND x.option_type = r.option_type AND x.strike = r.wing_strike
        WHERE r.wing_rank = 1 ORDER BY r.signal_date, r.option_type
        """, [min_contracts]).df()
    finally:
        con.unregister("_atm_candidates")


def vertical_candidates(wings: pd.DataFrame, *, haircut_per_side: float = 0.02,
                        min_contracts: int = 100) -> pd.DataFrame:
    """Debit (long ATM) and credit (short ATM) spreads for each CE/PE.

    Returns are P&L divided by entry payoff risk before exit costs. Proportional
    exit haircuts can make a mark-to-market loss larger than this denominator.
    A short ATM leg is always paired with a long OTM wing; no naked writing.
    """
    if not 0 <= haircut_per_side < 0.5 or min_contracts < 1:
        raise ValueError("invalid haircut or liquidity threshold")
    if wings.empty:
        return pd.DataFrame(columns=["signal_date", "structure", "option_type", "net_proxy"])
    needed = {"signal_date", "option_type", "spot", "spot_5_ago", "strike", "wing_strike",
              "entry_close", "exit_close", "wing_entry_close", "wing_exit_close",
              "entry_contracts", "exit_contracts", "wing_entry_contracts", "wing_exit_contracts"}
    if not needed.issubset(wings):
        raise ValueError(f"missing vertical fields: {sorted(needed - set(wings))}")
    w = wings.copy()
    width = abs(w["wing_strike"] - w["strike"])
    valid_quotes = (w[["entry_close", "exit_close", "wing_entry_close", "wing_exit_close"]].gt(0).all(axis=1)
                    & w[["entry_contracts", "exit_contracts", "wing_entry_contracts",
                         "wing_exit_contracts"]].ge(min_contracts).all(axis=1)
                    & width.gt(0))
    raw_entry = w["entry_close"] - w["wing_entry_close"]
    raw_exit = w["exit_close"] - w["wing_exit_close"]
    valid_quotes &= raw_entry.between(0, width) & raw_exit.between(0, width)
    h = haircut_per_side
    debit = w["entry_close"] * (1 + h) - w["wing_entry_close"] * (1 - h)
    exit_credit = w["exit_close"] * (1 - h) - w["wing_exit_close"] * (1 + h)
    credit = w["entry_close"] * (1 - h) - w["wing_entry_close"] * (1 + h)
    exit_debit = w["exit_close"] * (1 + h) - w["wing_exit_close"] * (1 - h)
    pieces = []
    for structure, entry, pnl, risk in (
        ("debit", debit, exit_credit - debit, debit),
        ("credit", credit, credit - exit_debit, width - credit),
    ):
        part = w.copy()
        part["structure"] = structure
        part["entry_premium_points"] = entry
        part["expiry_risk_points_before_exit_costs"] = risk
        # A stale EOD close can violate the no-arbitrage bounds even when contracts traded.
        good = valid_quotes & entry.gt(0) & entry.lt(width) & risk.gt(0)
        part["executable_proxy"] = good
        part["net_proxy"] = np.where(good, pnl / risk, np.nan)
        pieces.append(part)
    return pd.concat(pieces, ignore_index=True)
