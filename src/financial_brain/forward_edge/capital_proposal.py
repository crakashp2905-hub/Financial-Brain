"""Human-reviewable INR micro-capital and per-order risk proposal (FE-0137)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path


class CapitalProposalError(ValueError):
    """Capital-risk proposal is incomplete or internally inconsistent."""


@dataclass(frozen=True)
class CapitalRiskProposal:
    proposal_id: str
    account_alias: str
    strategy_id: str
    review_owner: str
    currency: str
    paper_capital_inr: float
    proposed_micro_capital_inr: float
    max_order_notional_inr: float
    max_risk_per_trade_inr: float
    max_daily_loss_inr: float
    max_drawdown_inr: float
    max_open_positions: int
    max_orders_per_session: int
    minimum_adv_notional_inr: float
    maximum_spread_bps: float
    estimated_round_trip_cost_inr: float
    minimum_expected_gross_pnl_per_trade_inr: float
    max_cost_to_expected_pnl_ratio: float
    option_margin_verified: bool
    physical_ordering_enabled: bool = False

    def __post_init__(self) -> None:
        if not all(value.strip() for value in
                   (self.proposal_id, self.account_alias, self.strategy_id, self.review_owner)):
            raise CapitalProposalError("proposal, account alias, strategy and reviewer are required")
        if self.currency != "INR":
            raise CapitalProposalError("this proposal template is specific to INR risk")
        monetary = (self.paper_capital_inr, self.proposed_micro_capital_inr,
                    self.max_order_notional_inr, self.max_risk_per_trade_inr,
                    self.max_daily_loss_inr, self.max_drawdown_inr,
                    self.minimum_adv_notional_inr, self.estimated_round_trip_cost_inr,
                    self.minimum_expected_gross_pnl_per_trade_inr)
        if any(not math.isfinite(value) or value < 0 for value in monetary):
            raise CapitalProposalError("capital and risk amounts must be finite and non-negative")
        if self.paper_capital_inr <= 0 or self.proposed_micro_capital_inr <= 0:
            raise CapitalProposalError("paper and proposed capital must be positive")
        if self.proposed_micro_capital_inr > self.paper_capital_inr:
            raise CapitalProposalError("proposed micro capital cannot exceed reviewed paper capital")
        if self.max_order_notional_inr <= 0 or self.max_order_notional_inr > self.proposed_micro_capital_inr:
            raise CapitalProposalError("max order notional must fit within proposed micro capital")
        if not 0 < self.max_risk_per_trade_inr <= self.max_daily_loss_inr <= self.max_drawdown_inr:
            raise CapitalProposalError("risk per trade <= daily loss <= max drawdown is required")
        if self.max_drawdown_inr > self.proposed_micro_capital_inr:
            raise CapitalProposalError("max drawdown cannot exceed proposed micro capital")
        if type(self.max_open_positions) is not int or self.max_open_positions < 1:
            raise CapitalProposalError("max_open_positions must be a positive integer")
        if type(self.max_orders_per_session) is not int or self.max_orders_per_session < 1:
            raise CapitalProposalError("max_orders_per_session must be a positive integer")
        if not math.isfinite(self.maximum_spread_bps) or self.maximum_spread_bps <= 0:
            raise CapitalProposalError("maximum spread must be finite and positive")
        if not math.isfinite(self.max_cost_to_expected_pnl_ratio) or not 0 < self.max_cost_to_expected_pnl_ratio < 1:
            raise CapitalProposalError("cost-to-expected-P&L limit must be between zero and one")
        if (self.minimum_adv_notional_inr <= 0 or self.estimated_round_trip_cost_inr <= 0
                or self.minimum_expected_gross_pnl_per_trade_inr <= 0):
            raise CapitalProposalError("ADV, estimated costs and expected gross P&L must be positive")
        if self.estimated_round_trip_cost_inr > (
                self.max_cost_to_expected_pnl_ratio * self.minimum_expected_gross_pnl_per_trade_inr):
            raise CapitalProposalError("estimated trade costs exceed the proposed P&L budget")
        if self.physical_ordering_enabled:
            raise CapitalProposalError("physical ordering is disabled by build policy")

    def body(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.body(), sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()


def render_capital_proposal(proposal: CapitalRiskProposal, output: Path) -> Path:
    """Write a review worksheet with explicit pending owner confirmation; never an order ticket."""
    fields = (
        ("Paper capital reviewed, INR", proposal.paper_capital_inr),
        ("Proposed micro capital, INR", proposal.proposed_micro_capital_inr),
        ("Maximum order notional, INR", proposal.max_order_notional_inr),
        ("Maximum risk per trade, INR", proposal.max_risk_per_trade_inr),
        ("Maximum daily loss, INR", proposal.max_daily_loss_inr),
        ("Maximum drawdown, INR", proposal.max_drawdown_inr),
        ("Maximum open positions", proposal.max_open_positions),
        ("Maximum orders per session", proposal.max_orders_per_session),
        ("Minimum ADV notional, INR", proposal.minimum_adv_notional_inr),
        ("Maximum spread, bps", proposal.maximum_spread_bps),
        ("Estimated round-trip cost, INR", proposal.estimated_round_trip_cost_inr),
        ("Minimum expected gross P&L per trade, INR", proposal.minimum_expected_gross_pnl_per_trade_inr),
        ("Maximum cost / expected P&L ratio", proposal.max_cost_to_expected_pnl_ratio),
        ("Options margin verified", proposal.option_margin_verified),
        ("Physical ordering enabled", proposal.physical_ordering_enabled),
    )
    rows = "".join(f"| {label} | {value} |\n" for label, value in fields)
    markdown = (f"# Capital-risk proposal: {proposal.proposal_id}\n\n"
                f"- Account alias: `{proposal.account_alias}`\n"
                f"- Strategy: `{proposal.strategy_id}`\n"
                f"- Review owner: {proposal.review_owner}\n"
                f"- Currency: {proposal.currency}\n"
                f"- Proposal SHA-256: `{proposal.digest()}`\n"
                "- Review status: **PENDING HUMAN REVIEW**\n"
                "- Manual confirmation recorded: **NO**\n\n"
                "This worksheet is not a capital approval or order instruction. Live cash and physical ordering remain disabled.\n\n"
                "| Limit | Proposed value |\n|---|---:|\n" + rows + "\n"
                "## Human review\n\n"
                "- [ ] Review broker, exchange and product permissions\n"
                "- [ ] Review fees, taxes, spread and impact assumptions against current evidence\n"
                "- [ ] Confirm margin and expiry settlement controls for derivatives\n"
                "- [ ] Confirm maximum loss, drawdown, liquidity and order limits\n"
                "- [ ] Record a separately scoped, expiring human approval\n"
                "- [ ] Confirm the system is still in PAPER mode; this proposal cannot enable live trading\n")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(markdown, encoding="utf-8")
    return destination
