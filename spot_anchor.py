"""
Spot Price Anchor & Real-Time Momentum Engine
Connects to Coinbase WebSocket stream (US-cloud compatible, zero geo-blocks)
to detect cross-market lead-lag opportunities and protect Polymarket quotes from adverse selection.
"""
import time
import asyncio
import logging
from collections import deque
from typing import Dict, Any, Optional
import aiohttp

logger = logging.getLogger("polymarket_bot.spot_anchor")

COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"
COINBASE_REST_URL = "https://api.coinbase.com/v2/prices/BTC-USD/spot"


class SpotAnchor:
    def __init__(self, volatility_threshold_pct: float = 0.35):
        self.volatility_threshold_pct = volatility_threshold_pct
        self.current_price: float = 0.0
        self.last_update_time: float = 0.0
        self.price_history_60s: deque = deque()  # (timestamp, price)
        self.velocity_1m_pct: float = 0.0
        self.is_volatile: bool = False
        self.status: str = "Initializing"
        self._running: bool = False

    async def start(self):
        """Continuously maintain WebSocket connection to Coinbase ticker."""
        self._running = True
        logger.info("Starting Coinbase Spot Anchor WebSocket stream...")

        sub_msg = {
            "type": "subscribe",
            "product_ids": ["BTC-USD"],
            "channels": ["ticker"]
        }

        while self._running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(COINBASE_WS_URL, timeout=10) as ws:
                        await ws.send_json(sub_msg)
                        self.status = "Streaming Live (Coinbase)"
                        logger.info("Connected to Coinbase WebSocket stream.")

                        async for msg in ws:
                            if not self._running:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = msg.json()
                                if data.get("type") == "ticker" and "price" in data:
                                    self._record_price(float(data["price"]))
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                logger.warning("Coinbase WS stream closed/errored. Reconnecting...")
                                break
            except Exception as e:
                logger.warning(f"Coinbase WebSocket error: {e}. Falling back to REST...")
                self.status = f"Reconnecting ({str(e)[:20]})"
                await self._fallback_rest_poll()
                await asyncio.sleep(5)

    async def _fallback_rest_poll(self):
        """Fallback polling via Coinbase REST API."""
        try:
            headers = {"User-Agent": "PolymarketPrecisionBot/1.0"}
            async with aiohttp.ClientSession() as session:
                async with session.get(COINBASE_REST_URL, headers=headers, timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        price = float(data.get("data", {}).get("amount", 0))
                        if price > 0:
                            self._record_price(price)
                            self.status = "Connected (REST Fallback)"
        except Exception as e:
            logger.error(f"Coinbase REST fallback failed: {e}")

    def _record_price(self, price: float):
        now = time.time()
        self.current_price = price
        self.last_update_time = now
        self.price_history_60s.append((now, price))

        # Evict data older than 60 seconds
        while self.price_history_60s and (now - self.price_history_60s[0][0]) > 60.0:
            self.price_history_60s.popleft()

        # Calculate 1-minute velocity
        if len(self.price_history_60s) > 1:
            oldest_price = self.price_history_60s[0][1]
            if oldest_price > 0:
                self.velocity_1m_pct = ((price - oldest_price) / oldest_price) * 100.0

        # Detect rapid volatility spike
        self.is_volatile = abs(self.velocity_1m_pct) >= self.volatility_threshold_pct

    def get_market_risk_signal(self, question: str, side: str) -> Dict[str, Any]:
        """
        Evaluate if a Polymarket prediction contract is facing adverse selection
        based on live spot price movement.
        """
        q_lower = question.lower()
        is_btc = "bitcoin" in q_lower or "btc" in q_lower

        if not is_btc or self.current_price <= 0:
            return {"action": "PROCEED", "reason": "Not a Bitcoin market"}

        # If high volatility is active, trigger Toxicity Shield
        if self.is_volatile:
            direction = "CRASHING" if self.velocity_1m_pct < 0 else "SURGING"
            return {
                "action": "CANCEL_AND_WAIT",
                "reason": f"Toxicity Shield Active: BTC {direction} ({self.velocity_1m_pct:+.2f}% in 60s)",
                "btc_price": self.current_price,
                "velocity": self.velocity_1m_pct,
            }

        return {
            "action": "PROCEED",
            "btc_price": self.current_price,
            "velocity": round(self.velocity_1m_pct, 3),
        }

    def get_status_summary(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "btc_price": round(self.current_price, 2),
            "velocity_1m_pct": round(self.velocity_1m_pct, 3),
            "is_volatile": self.is_volatile,
            "last_seen_sec_ago": round(time.time() - self.last_update_time, 1) if self.last_update_time else None,
        }
