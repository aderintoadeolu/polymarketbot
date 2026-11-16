"""
Market Scanner for Polymarket
Scans Gamma and CLOB APIs for active markets, wide spreads, and arbitrage opportunities.
"""
import json
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import requests

from config import GAMMA_API_URL, CLOB_API_URL, DEFAULT_HEADERS

logger = logging.getLogger("polymarket_bot.scanner")


@dataclass
class MarketSummary:
    id: str
    condition_id: str
    question: str
    slug: str
    end_date: str
    volume_24h: float
    total_volume: float
    liquidity: float
    outcomes: List[str]
    outcome_prices: List[float]
    clob_token_ids: List[str]
    spread: Optional[float] = None
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def price_sum(self) -> float:
        """Sum of outcome prices."""
        return sum(self.outcome_prices) if self.outcome_prices else 0.0

    @property
    def is_binary(self) -> bool:
        return len(self.outcomes) == 2 and len(self.clob_token_ids) == 2


class MarketScanner:
    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def fetch_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        active: bool = True,
        closed: bool = False,
        order: str = "volume24hr",
        ascending: bool = False,
    ) -> List[MarketSummary]:
        """Fetch active markets from Polymarket Gamma API."""
        url = f"{GAMMA_API_URL}/markets"
        params = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "order": order,
            "ascending": str(ascending).lower(),
        }

        try:
            resp = self.session.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch markets from Gamma API: {e}")
            return []

        summaries = []
        for item in data:
            summary = self._parse_market(item)
            if summary:
                summaries.append(summary)

        return summaries

    def _parse_market(self, item: Dict[str, Any]) -> Optional[MarketSummary]:
        try:
            market_id = str(item.get("id", ""))
            condition_id = item.get("conditionId", "")
            question = item.get("question", item.get("title", "Untitled"))
            slug = item.get("slug", "")
            end_date = item.get("endDate", "")

            # Volume & liquidity
            volume_24h = float(item.get("volume24hr", 0) or 0)
            total_volume = float(item.get("volume", 0) or 0)
            liquidity = float(item.get("liquidity", 0) or 0)

            # Outcomes
            outcomes_raw = item.get("outcomes", [])
            if isinstance(outcomes_raw, str):
                try:
                    outcomes = json.loads(outcomes_raw)
                except Exception:
                    outcomes = [x.strip() for x in outcomes_raw.split(",")]
            else:
                outcomes = outcomes_raw or []

            # Outcome prices
            prices_raw = item.get("outcomePrices", [])
            if isinstance(prices_raw, str):
                try:
                    prices = [float(p) for p in json.loads(prices_raw)]
                except Exception:
                    prices = []
            else:
                prices = [float(p) for p in prices_raw] if prices_raw else []

            # CLOB Token IDs
            token_ids_raw = item.get("clobTokenIds", [])
            if isinstance(token_ids_raw, str):
                try:
                    token_ids = json.loads(token_ids_raw)
                except Exception:
                    token_ids = []
            else:
                token_ids = token_ids_raw or []

            spread = float(item.get("spread", 0) or 0) if "spread" in item and item["spread"] is not None else None
            best_bid = float(item.get("bestBid", 0) or 0) if "bestBid" in item and item["bestBid"] is not None else None
            best_ask = float(item.get("bestAsk", 0) or 0) if "bestAsk" in item and item["bestAsk"] is not None else None

            return MarketSummary(
                id=market_id,
                condition_id=condition_id,
                question=question,
                slug=slug,
                end_date=end_date,
                volume_24h=volume_24h,
                total_volume=total_volume,
                liquidity=liquidity,
                outcomes=outcomes,
                outcome_prices=prices,
                clob_token_ids=token_ids,
                spread=spread,
                best_bid=best_bid,
                best_ask=best_ask,
                raw=item,
            )
        except Exception as e:
            logger.debug(f"Error parsing market item {item.get('id')}: {e}")
            return None

    def find_arbitrage_candidates(
        self, markets: List[MarketSummary], min_profit_pct: float = 0.01
    ) -> List[Dict[str, Any]]:
        """
        Identify binary markets where outcome prices sum to significantly less than 1.00
        (or significantly greater than 1.00 for short arbitrage / rebalancing).
        """
        candidates = []
        for m in markets:
            if not m.is_binary or len(m.outcome_prices) < 2:
                continue

            price_sum = m.outcome_prices[0] + m.outcome_prices[1]
            # If price_sum is less than (1 - min_profit_pct), buying 1 unit of each guarantees $1.00 payout
            if 0.10 < price_sum < (1.0 - min_profit_pct):
                profit_margin = 1.0 - price_sum
                candidates.append({
                    "market": m,
                    "type": "LONG_ARBITRAGE",
                    "price_sum": price_sum,
                    "profit_margin": profit_margin,
                    "profit_margin_pct": (profit_margin / price_sum) * 100,
                    "yes_price": m.outcome_prices[0],
                    "no_price": m.outcome_prices[1],
                })
            # Negative arbitrage (sum > 1.00)
            elif price_sum > (1.0 + min_profit_pct):
                overprice = price_sum - 1.0
                candidates.append({
                    "market": m,
                    "type": "OVERPRICED_SUM",
                    "price_sum": price_sum,
                    "profit_margin": overprice,
                    "profit_margin_pct": (overprice / price_sum) * 100,
                    "yes_price": m.outcome_prices[0],
                    "no_price": m.outcome_prices[1],
                })

        candidates.sort(key=lambda x: x["profit_margin"], reverse=True)
        return candidates

    def find_long_tail_opportunities(
        self,
        markets: List[MarketSummary],
        min_liquidity: float = 3000.0,
        min_volume: float = 1000.0,
        max_volume: float = 500000.0,
    ) -> List[MarketSummary]:
        """
        Find markets with healthy enough liquidity/volume to trade,
        but not so hyper-liquid that institutions have squeezed spreads to 0.1%.
        """
        results = []
        for m in markets:
            if m.liquidity >= min_liquidity and min_volume <= m.total_volume <= max_volume:
                results.append(m)

        # Sort by total volume descending
        results.sort(key=lambda x: x.total_volume, reverse=True)
        return results
