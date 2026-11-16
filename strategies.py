"""
Polymarket Trading Strategies
1. Binary Arbitrage Hunter (Yes + No < $1.00)
2. Long-Tail Market Maker (Spread Capture & Inventory Management)
"""
import logging
import time
from typing import Dict, List, Optional, Tuple, Any

from config import ARBITRAGE_MIN_PROFIT_PCT, MIN_SPREAD_THRESHOLD_PCT, MAX_POSITION_SIZE_USDC
from market_scanner import MarketSummary, MarketScanner
from orderbook import OrderBook, OrderBookFetcher
from paper_trader import PaperTrader

logger = logging.getLogger("polymarket_bot.strategies")


class ArbitrageHunter:
    """
    Detects and executes risk-neutral binary arbitrage:
    If BestAsk(Yes) + BestAsk(No) < 1.00, buying 1 unit of each guarantees $1.00 payout at resolution.
    """
    def __init__(self, paper_trader: PaperTrader, book_fetcher: OrderBookFetcher):
        self.trader = paper_trader
        self.book_fetcher = book_fetcher

    def evaluate_market(self, market: MarketSummary) -> Optional[Dict[str, Any]]:
        """
        Check live order books of both YES and NO tokens for immediate execution arbitrage.
        """
        if not market.is_binary or len(market.clob_token_ids) != 2:
            return None

        yes_token = market.clob_token_ids[0]
        no_token = market.clob_token_ids[1]

        yes_book = self.book_fetcher.fetch_order_book(yes_token)
        no_book = self.book_fetcher.fetch_order_book(no_token)

        if not yes_book or not no_book:
            return None

        if not yes_book.best_ask or not no_book.best_ask:
            return None

        sum_asks = yes_book.best_ask + no_book.best_ask
        gross_margin = 1.0 - sum_asks

        if gross_margin >= ARBITRAGE_MIN_PROFIT_PCT:
            # Calculate maximum executable volume at top of book
            max_shares = min(yes_book.best_ask_size, no_book.best_ask_size)
            max_cost = max_shares * sum_asks

            return {
                "market": market,
                "yes_token": yes_token,
                "no_token": no_token,
                "yes_ask": yes_book.best_ask,
                "no_ask": no_book.best_ask,
                "sum_asks": sum_asks,
                "gross_margin": gross_margin,
                "margin_pct": (gross_margin / sum_asks) * 100,
                "max_shares": max_shares,
                "max_cost_usdc": max_cost,
                "yes_book": yes_book,
                "no_book": no_book,
            }
        return None

    def execute_arbitrage(self, arb_opp: Dict[str, Any], budget_usdc: float = 50.0) -> bool:
        """
        Simulate simultaneous buy on YES and NO tokens to lock in guaranteed profit.
        """
        market = arb_opp["market"]
        yes_book = arb_opp["yes_book"]
        no_book = arb_opp["no_book"]

        spend_per_leg = min(budget_usdc / 2.0, MAX_POSITION_SIZE_USDC / 2.0)

        # Check liquidity & simulate execution
        trade_yes = self.trader.execute_buy(
            token_id=arb_opp["yes_token"],
            condition_id=market.condition_id,
            question=market.question,
            outcome="Yes",
            target_amount_usdc=spend_per_leg,
            book=yes_book,
        )

        trade_no = self.trader.execute_buy(
            token_id=arb_opp["no_token"],
            condition_id=market.condition_id,
            question=market.question,
            outcome="No",
            target_amount_usdc=spend_per_leg,
            book=no_book,
        )

        if trade_yes and trade_no:
            total_invested = trade_yes.cost_usdc + trade_no.cost_usdc
            shares_paired = min(trade_yes.shares, trade_no.shares)
            guaranteed_payout = shares_paired * 1.0  # Exactly 1 of the 2 will resolve to $1.00
            expected_profit = guaranteed_payout - total_invested

            logger.info(
                f"[ARB EXECUTED] {market.question[:40]}... | Invested: ${total_invested:.2f} | "
                f"Guaranteed Payout: ${guaranteed_payout:.2f} | Net Lock: +${expected_profit:.2f}"
            )
            return True
        return False


class MarketMaker:
    """
    Market Making Strategy for wide-spread, long-tail prediction markets.
    Simulates posting passive limit orders to earn the bid-ask spread while monitoring adverse selection.
    """
    def __init__(self, paper_trader: PaperTrader, book_fetcher: OrderBookFetcher):
        self.trader = paper_trader
        self.book_fetcher = book_fetcher

    def evaluate_spread(self, token_id: str, market_question: str) -> Optional[Dict[str, Any]]:
        book = self.book_fetcher.fetch_order_book(token_id)
        if not book or not book.best_bid or not book.best_ask or not book.spread:
            return None

        # Check if spread meets minimum threshold (e.g. >= 3%)
        if book.spread_pct and book.spread_pct >= (MIN_SPREAD_THRESHOLD_PCT * 100):
            my_bid = round(book.best_bid + 0.01, 2)
            my_ask = round(book.best_ask - 0.01, 2)

            if my_bid < my_ask:
                potential_margin = round(my_ask - my_bid, 4)
                return {
                    "token_id": token_id,
                    "question": market_question,
                    "market_bid": book.best_bid,
                    "market_ask": book.best_ask,
                    "spread": book.spread,
                    "spread_pct": book.spread_pct,
                    "target_bid": my_bid,
                    "target_ask": my_ask,
                    "target_margin": potential_margin,
                    "book": book,
                }
        return None
