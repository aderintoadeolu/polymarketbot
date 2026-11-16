"""
Paper Trading Engine for Polymarket
Simulates fills, slippage, latency, and portfolio tracking against real CLOB order books.
"""
import json
import logging
import time
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any
from pathlib import Path

from config import PORTFOLIO_FILE, PAPER_STARTING_BALANCE, MAX_POSITION_SIZE_USDC, MAX_PORTFOLIO_ALLOCATION_PCT
from orderbook import OrderBook

logger = logging.getLogger("polymarket_bot.paper_trader")


@dataclass
class Position:
    token_id: str
    condition_id: str
    question: str
    outcome: str
    shares: float
    avg_entry_price: float
    current_price: float
    created_at: float

    @property
    def cost_basis(self) -> float:
        return self.shares * self.avg_entry_price

    @property
    def market_value(self) -> float:
        return self.shares * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        return self.market_value - self.cost_basis

    @property
    def pnl_pct(self) -> float:
        return (self.unrealized_pnl / self.cost_basis * 100) if self.cost_basis > 0 else 0.0


@dataclass
class TradeRecord:
    trade_id: str
    timestamp: float
    token_id: str
    question: str
    outcome: str
    side: str  # "BUY" or "SELL"
    shares: float
    avg_price: float
    cost_usdc: float
    slippage_pct: float
    realized_pnl: float = 0.0


class PaperTrader:
    def __init__(self, portfolio_path: Optional[Path] = None):
        self.portfolio_path = portfolio_path or PORTFOLIO_FILE
        self.balance: float = PAPER_STARTING_BALANCE
        self.positions: Dict[str, Position] = {}
        self.trade_history: List[TradeRecord] = []
        self.total_realized_pnl: float = 0.0
        self.wins: int = 0
        self.losses: int = 0
        self.load_state()

    def load_state(self):
        """Load state from JSON if it exists."""
        if not self.portfolio_path.exists():
            return
        try:
            with open(self.portfolio_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.balance = float(data.get("balance", PAPER_STARTING_BALANCE))
            self.total_realized_pnl = float(data.get("total_realized_pnl", 0.0))
            self.wins = int(data.get("wins", 0))
            self.losses = int(data.get("losses", 0))

            self.positions = {}
            for token_id, pos_data in data.get("positions", {}).items():
                self.positions[token_id] = Position(**pos_data)

            self.trade_history = []
            for t in data.get("trade_history", []):
                self.trade_history.append(TradeRecord(**t))

            logger.info(f"Loaded paper portfolio: Balance=${self.balance:.2f}, Positions={len(self.positions)}")
        except Exception as e:
            logger.error(f"Error loading portfolio state: {e}")

    def save_state(self):
        """Save portfolio state to JSON file."""
        try:
            payload = {
                "balance": self.balance,
                "total_realized_pnl": self.total_realized_pnl,
                "wins": self.wins,
                "losses": self.losses,
                "positions": {tid: asdict(pos) for tid, pos in self.positions.items()},
                "trade_history": [asdict(t) for t in self.trade_history[-200:]],  # keep last 200 trades
                "updated_at": time.time(),
            }
            with open(self.portfolio_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving portfolio state: {e}")

    @property
    def total_equity(self) -> float:
        """Total portfolio value = Cash balance + value of all open positions."""
        position_value = sum(pos.market_value for pos in self.positions.values())
        return self.balance + position_value

    @property
    def total_unrealized_pnl(self) -> float:
        return sum(pos.unrealized_pnl for pos in self.positions.values())

    def execute_buy(
        self,
        token_id: str,
        condition_id: str,
        question: str,
        outcome: str,
        target_amount_usdc: float,
        book: OrderBook,
    ) -> Optional[TradeRecord]:
        """
        Simulate an aggressive market BUY order by consuming asks in the real order book.
        """
        # Risk checks
        if target_amount_usdc > self.balance:
            logger.warning(f"Insufficient funds: Have ${self.balance:.2f}, requested ${target_amount_usdc:.2f}")
            return None

        # Position size limits
        max_allowed = min(MAX_POSITION_SIZE_USDC, self.total_equity * MAX_PORTFOLIO_ALLOCATION_PCT)
        spend = min(target_amount_usdc, max_allowed)

        # Walk the book
        shares, avg_price, slippage = book.calculate_buy_execution(spend)
        if shares <= 0:
            logger.warning(f"No liquidity available to buy token {token_id}")
            return None

        actual_cost = shares * avg_price
        self.balance -= actual_cost

        # Update position
        if token_id in self.positions:
            pos = self.positions[token_id]
            new_shares = pos.shares + shares
            new_avg_price = (pos.cost_basis + actual_cost) / new_shares
            pos.shares = new_shares
            pos.avg_entry_price = new_avg_price
            pos.current_price = avg_price
        else:
            self.positions[token_id] = Position(
                token_id=token_id,
                condition_id=condition_id,
                question=question,
                outcome=outcome,
                shares=shares,
                avg_entry_price=avg_price,
                current_price=avg_price,
                created_at=time.time(),
            )

        trade = TradeRecord(
            trade_id=f"T-BUY-{int(time.time()*1000)}",
            timestamp=time.time(),
            token_id=token_id,
            question=question,
            outcome=outcome,
            side="BUY",
            shares=shares,
            avg_price=avg_price,
            cost_usdc=actual_cost,
            slippage_pct=slippage,
        )
        self.trade_history.append(trade)
        self.save_state()
        return trade

    def execute_sell(
        self,
        token_id: str,
        shares_to_sell: float,
        book: OrderBook,
    ) -> Optional[TradeRecord]:
        """
        Simulate an aggressive market SELL order by consuming bids in the real order book.
        """
        if token_id not in self.positions:
            logger.warning(f"Cannot sell: No open position for {token_id}")
            return None

        pos = self.positions[token_id]
        shares = min(shares_to_sell, pos.shares)

        usdc_received, avg_price, slippage = book.calculate_sell_execution(shares)
        if shares <= 0 or usdc_received <= 0:
            logger.warning(f"No bid liquidity available to sell token {token_id}")
            return None

        cost_of_sold = shares * pos.avg_entry_price
        realized = usdc_received - cost_of_sold

        self.balance += usdc_received
        self.total_realized_pnl += realized

        if realized >= 0:
            self.wins += 1
        else:
            self.losses += 1

        # Adjust position
        pos.shares -= shares
        pos.current_price = avg_price
        if pos.shares <= 0.0001:
            del self.positions[token_id]

        trade = TradeRecord(
            trade_id=f"T-SELL-{int(time.time()*1000)}",
            timestamp=time.time(),
            token_id=token_id,
            question=pos.question,
            outcome=pos.outcome,
            side="SELL",
            shares=shares,
            avg_price=avg_price,
            cost_usdc=usdc_received,
            slippage_pct=slippage,
            realized_pnl=realized,
        )
        self.trade_history.append(trade)
        self.save_state()
        return trade

    def update_market_prices(self, price_map: Dict[str, float]):
        """Update mark-to-market valuations for open positions."""
        for token_id, pos in self.positions.items():
            if token_id in price_map:
                pos.current_price = price_map[token_id]
        self.save_state()
