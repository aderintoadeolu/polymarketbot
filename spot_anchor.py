"""
Spot Price Anchor & Real-Time Momentum Engine
Connects to Binance Public WebSocket stream to detect cross-market lead-lag opportunities
and protect Polymarket quotes from adverse selection during rapid spot market moves.
"""
import time
import asyncio
import logging
from collections import deque
from typing import Dict, Any, Optional
import aiohttp

logger = logging.getLogger("polymarket_bot.spot_anchor")

BINANCE_WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@ticker"
BINANCE_REST_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"


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
        """Continuously maintain WebSocket connection to Binance ticker."""
        self._running = True
        logger.info("Starting Binance Spot Anchor WebSocket stream...")

        while self._running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(BINANCE_WS_URL, timeout=10) as ws:
                        self.status = "Connected (Streaming)"
                        logger.info("Connected to Binance WebSocket stream.")

                        async for msg in ws:
                            if not self._running:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = msg.json()
                                price_str = data.get("c")  # Last trade price
                                if price_str:
                                    self._record_price(float(price_str))
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                logger.warning("Binance WS stream closed/errored. Reconnecting...")
                                break
            except Exception as e:
                logger.warning(f"Binance WebSocket error: {e}. Falling back to REST...")
                self.status = f"Reconnecting ({str(e)[:20]})"
                await self._fallback_rest_poll()
                await asyncio.sleep(5)

    async def _fallback_rest_poll(self):
        """Fallback polling via REST if WebSocket fails."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(BINANCE_REST_URL, timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        price = float(data.get("price", 0))
                        if price > 0:
                            self._record_price(price)
                            self.status = "Connected (REST Fallback)"
        except Exception as e:
            logger.error(f"Binance REST fallback failed: {e}")

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
