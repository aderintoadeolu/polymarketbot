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


from dataclasses import dataclass, field
import uuid


@dataclass
class MMQuote:
    quote_id: str
    token_id: str
    condition_id: str
    question: str
    outcome: str
    side: str  # "BUY" or "SELL"
    quote_price: float
    shares: float
    market_bid_at_quote: float
    market_ask_at_quote: float
    spread_at_quote: float
    created_at: float
    status: str = "OPEN"  # "OPEN", "FILLED", "CANCELLED"
    fill_time: Optional[float] = None
    realized_profit: float = 0.0


class ActiveMarketMaker:
    """
    Active Paper Market Maker:
    Simulates posting passive limit orders inside wide spreads, tracking fills,
    rebalancing inventory, and measuring adverse selection in real-time.
    """
    def __init__(
        self,
        paper_trader: PaperTrader,
        min_spread_pct: float = 4.0,
        quote_size_usdc: float = 25.0,
        max_active_markets: int = 5,
    ):
        self.trader = paper_trader
        self.min_spread_pct = min_spread_pct
        self.quote_size_usdc = quote_size_usdc
        self.max_active_markets = max_active_markets
        self.active_quotes: Dict[str, MMQuote] = {}
        self.history: List[MMQuote] = []

    def update(self, markets: List[MarketSummary], fetcher: OrderBookFetcher) -> Dict[str, Any]:
        """Runs on each tick: checks fills, cancels stale quotes, and places new quotes."""
        fills = self._check_fills(fetcher)
        self._prune_stale(fetcher)
        new_quotes = self._quote_opportunities(markets, fetcher)

        open_quotes = [q for q in self.active_quotes.values() if q.status == "OPEN"]
        return {
            "open_count": len(open_quotes),
            "fills_this_tick": fills,
            "new_quotes": new_quotes,
            "total_fills": len(self.history),
        }

    def _check_fills(self, fetcher: OrderBookFetcher) -> int:
        fills_count = 0
        now = time.time()

        for qid, q in list(self.active_quotes.items()):
            if q.status != "OPEN":
                continue

            book = fetcher.fetch_order_book(q.token_id)
            if not book or not book.best_bid or not book.best_ask:
                continue

            # Check BUY Fill
            if q.side == "BUY":
                # Market has traded into or below our bid price
                if book.best_bid >= q.quote_price or book.best_ask <= q.quote_price:
                    q.status = "FILLED"
                    q.fill_time = now
                    trade = self.trader.execute_limit_fill(
                        token_id=q.token_id,
                        condition_id=q.condition_id,
                        question=q.question,
                        outcome=q.outcome,
                        side="BUY",
                        price=q.quote_price,
                        shares=q.shares,
                    )
                    if trade:
                        fills_count += 1
                        logger.info(f"🎯 [MM FILL - BUY] {q.question[:35]}... {q.shares} shares @ ${q.quote_price:.3f}")
                        # Immediately post the counter-SELL quote to capture the spread!
                        sell_price = round(max(book.best_ask - 0.01, q.quote_price + 0.01), 3)
                        sell_qid = f"Q-SELL-{int(now*1000)}-{uuid.uuid4().hex[:4]}"
                        self.active_quotes[sell_qid] = MMQuote(
                            quote_id=sell_qid,
                            token_id=q.token_id,
                            condition_id=q.condition_id,
                            question=q.question,
                            outcome=q.outcome,
                            side="SELL",
                            quote_price=sell_price,
                            shares=q.shares,
                            market_bid_at_quote=book.best_bid,
                            market_ask_at_quote=book.best_ask,
                            spread_at_quote=round(book.spread or 0.0, 3),
                            created_at=now,
                        )

            # Check SELL Fill
            elif q.side == "SELL":
                # Market traded up into or above our ask price
                if book.best_ask <= q.quote_price or book.best_bid >= q.quote_price:
                    q.status = "FILLED"
                    q.fill_time = now
                    trade = self.trader.execute_limit_fill(
                        token_id=q.token_id,
                        condition_id=q.condition_id,
                        question=q.question,
                        outcome=q.outcome,
                        side="SELL",
                        price=q.quote_price,
                        shares=q.shares,
                    )
                    if trade:
                        fills_count += 1
                        q.realized_profit = trade.realized_pnl
                        self.history.append(q)
                        logger.info(f"💰 [MM FILL - SPREAD CAPTURED] {q.question[:35]}... PnL: +${trade.realized_pnl:.2f}")

        return fills_count

    def _prune_stale(self, fetcher: OrderBookFetcher, max_age_seconds: float = 600.0):
        """Cancel quotes if open too long or if market moves strongly away."""
        now = time.time()
        for qid, q in list(self.active_quotes.items()):
            if q.status != "OPEN":
                continue

            # Cancel expired quotes
            if now - q.created_at > max_age_seconds:
                q.status = "CANCELLED"
                continue

            # Cancel if price drifts > 6% away (adverse selection safety)
            book = fetcher.fetch_order_book(q.token_id)
            if book and book.midpoint:
                drift = abs(book.midpoint - q.quote_price) / q.quote_price
                if drift > 0.06:
                    q.status = "CANCELLED"

    def _quote_opportunities(self, markets: List[MarketSummary], fetcher: OrderBookFetcher) -> int:
        open_tokens = {q.token_id for q in self.active_quotes.values() if q.status == "OPEN"}
        if len(open_tokens) >= self.max_active_markets:
            return 0

        quotes_created = 0
        now = time.time()

        for m in markets:
            if len(open_tokens) >= self.max_active_markets:
                break
            if not m.clob_token_ids or m.liquidity < 3000.0 or m.total_volume < 1000.0:
                continue

            token_id = m.clob_token_ids[0]
            if token_id in open_tokens:
                continue

            book = fetcher.fetch_order_book(token_id)
            if not book or not book.best_bid or not book.best_ask or not book.spread_pct:
                continue

            if book.spread_pct >= self.min_spread_pct and book.best_bid >= 0.03:
                my_bid = round(book.best_bid + 0.01, 3)
                if my_bid < book.best_ask:
                    shares = round(self.quote_size_usdc / my_bid, 1)
                    qid = f"Q-BUY-{int(now*1000)}-{uuid.uuid4().hex[:4]}"
                    self.active_quotes[qid] = MMQuote(
                        quote_id=qid,
                        token_id=token_id,
                        condition_id=m.condition_id,
                        question=m.question,
                        outcome=m.outcomes[0] if m.outcomes else "Yes",
                        side="BUY",
                        quote_price=my_bid,
                        shares=shares,
                        market_bid_at_quote=book.best_bid,
                        market_ask_at_quote=book.best_ask,
                        spread_at_quote=book.spread or 0.0,
                        created_at=now,
                    )
                    open_tokens.add(token_id)
                    quotes_created += 1
                    logger.info(f"✨ [MM QUOTE] Bidding ${my_bid:.3f} on '{m.question[:35]}...' (Market Spread: {book.spread_pct:.1f}%)")

        return quotes_created

    def get_active_quotes_list(self) -> List[Dict[str, Any]]:
        """Return list of active and recent quotes for UI display."""
        all_quotes = list(self.active_quotes.values())
        all_quotes.sort(key=lambda x: x.created_at, reverse=True)
        results = []
        for q in all_quotes[:15]:
            results.append({
                "quote_id": q.quote_id,
                "question": q.question,
                "outcome": q.outcome,
                "side": q.side,
                "price": q.quote_price,
                "shares": q.shares,
                "market_bid": q.market_bid_at_quote,
                "market_ask": q.market_ask_at_quote,
                "spread_pct": round((q.spread_at_quote / ((q.market_bid_at_quote + q.market_ask_at_quote) / 2.0 or 1.0)) * 100, 1),
                "status": q.status,
                "profit": q.realized_profit,
                "age_sec": round(time.time() - q.created_at, 0),
            })
        return results
