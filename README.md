# Polymarket Trading & Paper Simulation Bot

A quantitative trading framework and paper execution suite for Polymarket, designed around the official Polymarket CLOB and Gamma APIs.

## Directory Structure
```
polymarket_bot/
├── .venv/                   # Python virtual environment
├── config.py                # Configuration, endpoints, risk parameters
├── market_scanner.py        # Gamma API market discovery & arb candidate filtering
├── orderbook.py             # Live CLOB L2 depth, bid/ask parsing & slippage calculator
├── paper_trader.py          # Realistic execution engine, position tracking, PnL accounting
├── strategies.py            # ArbitrageHunter (Yes+No < 1.00) & MarketMaker (spread capture)
├── telemetry.py             # Opportunity logger & decay/lifespan tracker
├── live_trader.py           # py-clob-client integration for live Polygon wallet execution
├── main.py                  # Rich CLI dashboard & runner
├── requirements.txt         # Dependencies
├── .env.example             # Example environment variables
├── telemetry_opportunities.csv # Harvested opportunity logs
└── paper_portfolio.json     # Saved portfolio state (cash, positions, trade history)
```

## Quick Start CLI Commands

Activate the environment or run via the virtual environment's Python:

### 1. Scan Top Active Markets
```bash
python main.py scan --limit 15 --sort volume24hr
```

### 2. Identify Arbitrage Opportunities (Yes + No < $1.00)
```bash
python main.py arb --limit 50 --min-margin 0.01
```

### 3. Scan Long-Tail Markets with Wide Spreads
```bash
python main.py longtail --limit 50 --min-liq 3000 --min-vol 1000 --show 10
```

### 4. Inspect Live L2 Order Book Depth
```bash
python main.py book <TOKEN_ID>
```

### 5. Simulate a Paper Trade (Market Buy / Sell)
```bash
# Buy $25 of shares (walks the asks and applies realistic slippage)
python main.py trade <TOKEN_ID> --side buy --amount 25.0

# Sell shares (walks the bids and calculates realized PnL)
python main.py trade <TOKEN_ID> --side sell --shares 10.0
```

### 6. View Portfolio Dashboard
```bash
python main.py portfolio
```

### 7. View Harvested Telemetry & Edge Report
```bash
python main.py telemetry
```

### 8. Run Automated Monitoring Daemon
```bash
# Runs continuous scanning, logging all spreads and arbitrage to CSV
python main.py daemon --interval 20 --limit 50
```

## Moving to Live Trading
When you are ready to transition from paper simulation to live capital:
1. Copy `.env.example` to `.env`.
2. Add your Polygon `PRIVATE_KEY` (funded with MATIC for gas and USDC for collateral).
3. `live_trader.py` will automatically derive the necessary CLOB API credentials using EIP-712 signatures.
