"""
Live Trading Execution Module for Polymarket CLOB
Uses official py-clob-client with EIP-712 signing on Polygon.
"""
import logging
from typing import Optional, Dict, Any, List

from config import (
    CLOB_API_URL,
    POLYGON_CHAIN_ID,
    PRIVATE_KEY,
    POLYMARKET_API_KEY,
    POLYMARKET_API_SECRET,
    POLYMARKET_PASSPHRASE,
)

logger = logging.getLogger("polymarket_bot.live_trader")


class LiveTrader:
    def __init__(
        self,
        private_key: Optional[str] = None,
        chain_id: int = POLYGON_CHAIN_ID,
        host: str = CLOB_API_URL,
    ):
        self.key = private_key or PRIVATE_KEY
        self.chain_id = chain_id
        self.host = host
        self.client = None
        self._is_ready = False

        if not self.key or len(self.key.strip()) < 32:
            logger.info("LiveTrader: No private key provided. Running in read-only / simulation mode.")
            return

        self._init_client()

    def _init_client(self):
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds

            # Format private key
            key_clean = self.key.strip()
            if not key_clean.startswith("0x") and len(key_clean) == 64:
                key_clean = "0x" + key_clean

            # Instantiate client
            self.client = ClobClient(
                host=self.host,
                key=key_clean,
                chain_id=self.chain_id,
            )

            # Check if API credentials provided; if not, derive them using L1 signature
            if POLYMARKET_API_KEY and POLYMARKET_API_SECRET and POLYMARKET_PASSPHRASE:
                creds = ApiCreds(
                    api_key=POLYMARKET_API_KEY,
                    api_secret=POLYMARKET_API_SECRET,
                    api_passphrase=POLYMARKET_PASSPHRASE,
                )
                self.client.set_api_creds(creds)
                logger.info("LiveTrader: Configured with existing API credentials.")
            else:
                logger.info("LiveTrader: Deriving CLOB API credentials from private key...")
                creds = self.client.create_or_derive_api_creds()
                self.client.set_api_creds(creds)
                logger.info(f"LiveTrader: API credentials successfully derived for {self.client.get_address()}")

            self._is_ready = True
        except Exception as e:
            logger.error(f"Failed to initialize LiveTrader: {e}")
            self._is_ready = False

    @property
    def is_ready(self) -> bool:
        return self._is_ready and self.client is not None

    def get_address(self) -> Optional[str]:
        if not self.is_ready:
            return None
        return self.client.get_address()

    def get_balance_and_allowance(self) -> Optional[Dict[str, Any]]:
        """Fetch USDC balance and CLOB allowance."""
        if not self.is_ready:
            return None
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            return self.client.get_balance_allowance(params)
        except Exception as e:
            logger.error(f"Error fetching balance/allowance: {e}")
            return None

    def place_limit_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,  # "BUY" or "SELL"
    ) -> Optional[Dict[str, Any]]:
        """
        Place a signed limit order on the Polymarket CLOB.
        """
        if not self.is_ready:
            logger.error("LiveTrader is not ready or no private key configured.")
            return None

        try:
            from py_clob_client.clob_types import OrderArgs, OrderType

            # Polymarket order args
            order_args = OrderArgs(
                token_id=token_id,
                price=round(price, 2),
                size=round(size, 2),
                side=side.upper(),
            )
            resp = self.client.create_and_post_order(order_args, order_type=OrderType.GTC)
            logger.info(f"Limit order submitted: {side} {size} @ ${price:.2f} | Resp: {resp}")
            return resp
        except Exception as e:
            logger.error(f"Failed to place limit order: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order by ID."""
        if not self.is_ready:
            return False
        try:
            resp = self.client.cancel(order_id)
            logger.info(f"Cancelled order {order_id}: {resp}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def cancel_all_orders(self) -> bool:
        """Cancel all open orders across all markets."""
        if not self.is_ready:
            return False
        try:
            resp = self.client.cancel_all()
            logger.info(f"Cancelled all open orders: {resp}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel all orders: {e}")
            return False

    def get_open_orders(self) -> List[Dict[str, Any]]:
        """Fetch list of open orders."""
        if not self.is_ready:
            return []
        try:
            from py_clob_client.clob_types import OpenOrderParams
            return self.client.get_orders(OpenOrderParams())
        except Exception as e:
            logger.error(f"Failed to fetch open orders: {e}")
            return []
