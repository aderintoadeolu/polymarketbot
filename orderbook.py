"""
Order Book and Market Depth Module for Polymarket CLOB
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple, Any
import requests

from config import CLOB_API_URL, DEFAULT_HEADERS

logger = logging.getLogger("polymarket_bot.orderbook")


@dataclass
class OrderLevel:
    price: float
    size: float  # Number of shares

    @property
    def value(self) -> float:
        return self.price * self.size


@dataclass
class OrderBook:
    token_id: str
    bids: List[OrderLevel] = field(default_factory=list)  # Sorted high to low
    asks: List[OrderLevel] = field(default_factory=list)  # Sorted low to high
    timestamp: float = 0.0

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None

    @property
    def best_bid_size(self) -> float:
        return self.bids[0].size if self.bids else 0.0

    @property
    def best_ask_size(self) -> float:
        return self.asks[0].size if self.asks else 0.0

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid is not None and self.best_ask is not None:
            return round(self.best_ask - self.best_bid, 4)
        return None

    @property
    def spread_pct(self) -> Optional[float]:
        if self.best_bid is not None and self.best_ask is not None and self.midpoint:
            return round((self.spread / self.midpoint) * 100, 2)
        return None

    @property
    def midpoint(self) -> Optional[float]:
        if self.best_bid is not None and self.best_ask is not None:
            return round((self.best_bid + self.best_ask) / 2.0, 4)
        return None

    @property
    def total_bid_depth_usdc(self) -> float:
        return sum(b.value for b in self.bids)

    @property
    def total_ask_depth_usdc(self) -> float:
        return sum(a.value for a in self.asks)

    def calculate_buy_execution(self, target_spend_usdc: float) -> Tuple[float, float, float]:
        """
        Simulate market BUY order by walking the asks.
        Returns: (total_shares_acquired, weighted_avg_price, slippage_pct)
        """
        if not self.asks or target_spend_usdc <= 0:
            return 0.0, 0.0, 0.0

        remaining_cash = target_spend_usdc
        total_shares = 0.0
        best_price = self.asks[0].price

        for level in self.asks:
            level_cost = level.price * level.size
            if remaining_cash <= level_cost:
                shares = remaining_cash / level.price
                total_shares += shares
                remaining_cash = 0.0
                break
            else:
                total_shares += level.size
                remaining_cash -= level_cost

        if total_shares == 0:
            return 0.0, 0.0, 0.0

        spent = target_spend_usdc - remaining_cash
        avg_price = spent / total_shares
        slippage_pct = ((avg_price - best_price) / best_price) * 100 if best_price > 0 else 0.0

        return total_shares, avg_price, slippage_pct

    def calculate_sell_execution(self, shares_to_sell: float) -> Tuple[float, float, float]:
        """
        Simulate market SELL order by walking the bids.
        Returns: (total_usdc_received, weighted_avg_price, slippage_pct)
        """
        if not self.bids or shares_to_sell <= 0:
            return 0.0, 0.0, 0.0

        remaining_shares = shares_to_sell
        total_usdc = 0.0
        best_price = self.bids[0].price

        for level in self.bids:
            if remaining_shares <= level.size:
                total_usdc += remaining_shares * level.price
                remaining_shares = 0.0
                break
            else:
                total_usdc += level.size * level.price
                remaining_shares -= level.size

        sold_shares = shares_to_sell - remaining_shares
        if sold_shares == 0:
            return 0.0, 0.0, 0.0

        avg_price = total_usdc / sold_shares
        slippage_pct = ((best_price - avg_price) / best_price) * 100 if best_price > 0 else 0.0

        return total_usdc, avg_price, slippage_pct


class OrderBookFetcher:
    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def fetch_order_book(self, token_id: str) -> Optional[OrderBook]:
        """Fetch live L2 order book for a given token ID from CLOB."""
        url = f"{CLOB_API_URL}/book"
        params = {"token_id": token_id}

        try:
            resp = self.session.get(url, params=params, timeout=8)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch order book for token {token_id}: {e}")
            return None

        # Parse bids (sort descending)
        bids = []
        for b in data.get("bids", []):
            try:
                bids.append(OrderLevel(price=float(b["price"]), size=float(b["size"])))
            except (ValueError, KeyError):
                continue
        bids.sort(key=lambda x: x.price, reverse=True)

        # Parse asks (sort ascending)
        asks = []
        for a in data.get("asks", []):
            try:
                asks.append(OrderLevel(price=float(a["price"]), size=float(a["size"])))
            except (ValueError, KeyError):
                continue
        asks.sort(key=lambda x: x.price)

        return OrderBook(
            token_id=token_id,
            bids=bids,
            asks=asks,
            timestamp=float(data.get("timestamp", 0) or 0),
        )
