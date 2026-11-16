"""
Polymarket Bot Configuration
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env if present
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Polymarket API Endpoints
GAMMA_API_URL = os.getenv("GAMMA_API_URL", "https://gamma-api.polymarket.com")
CLOB_API_URL = os.getenv("CLOB_API_URL", "https://clob.polymarket.com")
CLOB_WS_URL = os.getenv("CLOB_WS_URL", "wss://ws-subscriptions-clob.polymarket.com/ws/market")

# Polygon Network
POLYGON_CHAIN_ID = int(os.getenv("POLYGON_CHAIN_ID", "137"))
POLYGON_RPC_URL = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")

# Request Headers (to prevent Cloudflare/User-Agent blocks)
DEFAULT_HEADERS = {
    "User-Agent": "PolymarketBot/1.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)",
    "Accept": "application/json",
}

# Optional Credentials for Live Trading (Keep empty for paper trading)
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_API_SECRET = os.getenv("POLYMARKET_API_SECRET", "")
POLYMARKET_PASSPHRASE = os.getenv("POLYMARKET_PASSPHRASE", "")

# Paper Trading & Simulation Settings
PAPER_STARTING_BALANCE = float(os.getenv("PAPER_STARTING_BALANCE", "1000.0"))  # in USDC
PORTFOLIO_FILE = BASE_DIR / "paper_portfolio.json"

# Risk & Strategy Defaults
MIN_LIQUIDITY_USDC = float(os.getenv("MIN_LIQUIDITY_USDC", "5000.0"))
MAX_POSITION_SIZE_USDC = float(os.getenv("MAX_POSITION_SIZE_USDC", "100.0"))
MAX_PORTFOLIO_ALLOCATION_PCT = float(os.getenv("MAX_PORTFOLIO_ALLOCATION_PCT", "0.20"))  # Max 20% in one market
MIN_SPREAD_THRESHOLD_PCT = float(os.getenv("MIN_SPREAD_THRESHOLD_PCT", "0.03"))  # 3% spread minimum for market making
ARBITRAGE_MIN_PROFIT_PCT = float(os.getenv("ARBITRAGE_MIN_PROFIT_PCT", "0.01"))  # 1% minimum profit margin
SIMULATED_LATENCY_MS = int(os.getenv("SIMULATED_LATENCY_MS", "150"))  # 150ms roundtrip delay
