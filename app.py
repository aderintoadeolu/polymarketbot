"""
Polymarket Cloud Web Dashboard & Background Daemon
Zero-dependency web runner built with aiohttp.
Runs 24/7 on Hugging Face Spaces, Render, or any cloud container.
"""
import os
import time
import asyncio
import logging
from pathlib import Path
from aiohttp import web

from config import PAPER_STARTING_BALANCE
from market_scanner import MarketScanner
from orderbook import OrderBookFetcher
from paper_trader import PaperTrader
from strategies import ArbitrageHunter
from telemetry import TelemetryLogger

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cloud_bot")

PORT = int(os.getenv("PORT", "7860"))  # 7860 is Hugging Face default, Render passes $PORT
INTERVAL = int(os.getenv("INTERVAL", "20"))
MIN_MARGIN = float(os.getenv("MIN_MARGIN", "0.01"))
MIN_SPREAD = float(os.getenv("MIN_SPREAD", "0.03"))

scanner = MarketScanner()
fetcher = OrderBookFetcher()
trader = PaperTrader()
hunter = ArbitrageHunter(trader, fetcher)
telemetry = TelemetryLogger()

loop_state = {
    "status": "Starting...",
    "iterations": 0,
    "last_scan_time": "Never",
    "opportunities_found": 0,
}


async def background_scanner_loop():
    """Background task that runs continuously in the cloud."""
    logger.info("Starting background Polymarket scanner loop...")
    loop_state["status"] = "Active"

    while True:
        try:
            loop_state["iterations"] += 1
            now_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            loop_state["last_scan_time"] = now_str

            # 1. Fetch active markets
            markets = await asyncio.to_thread(scanner.fetch_markets, limit=40, order="volume24hr")

            # 2. Check for Arbitrage
            candidates = scanner.find_arbitrage_candidates(markets, min_profit_pct=MIN_MARGIN)
            for c in candidates:
                if c["type"] == "LONG_ARBITRAGE":
                    opp = await asyncio.to_thread(hunter.evaluate_market, c["market"])
                    if opp:
                        loop_state["opportunities_found"] += 1
                        telemetry.record_opportunity(
                            market_id=opp["market"].id,
                            question=opp["market"].question,
                            opp_type="LONG_ARBITRAGE",
                            yes_ask=opp["yes_ask"],
                            no_ask=opp["no_ask"],
                            sum_asks=opp["sum_asks"],
                            margin_pct=opp["margin_pct"],
                            spread_pct=0.0,
                            available_depth_usdc=opp["max_cost_usdc"],
                            volume_24h=opp["market"].volume_24h,
                            liquidity=opp["market"].liquidity,
                        )

            # 3. Check for Long-Tail Wide Spreads
            longtail = scanner.find_long_tail_opportunities(
                markets, min_liquidity=3000.0, min_volume=1000.0, max_volume=500000.0
            )
            for m in longtail[:10]:
                if not m.clob_token_ids:
                    continue
                book = await asyncio.to_thread(fetcher.fetch_order_book, m.clob_token_ids[0])
                if book and book.spread_pct and book.spread_pct >= (MIN_SPREAD * 100):
                    telemetry.record_opportunity(
                        market_id=m.id,
                        question=m.question,
                        opp_type="WIDE_SPREAD",
                        yes_ask=book.best_ask or 0.0,
                        no_ask=0.0,
                        sum_asks=0.0,
                        margin_pct=0.0,
                        spread_pct=book.spread_pct,
                        available_depth_usdc=book.best_ask_size * (book.best_ask or 0.0),
                        volume_24h=m.volume_24h,
                        liquidity=m.liquidity,
                    )

            # Prune stale
            telemetry.prune_expired(max_idle_sec=INTERVAL * 3)

        except Exception as e:
            logger.error(f"Error in scanner loop: {e}")
            loop_state["status"] = f"Error: {str(e)[:30]}"

        await asyncio.sleep(INTERVAL)


async def handle_status_api(request):
    """JSON API returning current live stats."""
    stats = telemetry.get_summary_stats()
    return web.json_response({
        "loop_state": loop_state,
        "portfolio": {
            "balance": round(trader.balance, 2),
            "total_equity": round(trader.total_equity, 2),
            "realized_pnl": round(trader.total_realized_pnl, 2),
            "unrealized_pnl": round(trader.total_unrealized_pnl, 2),
            "wins": trader.wins,
            "losses": trader.losses,
            "positions_count": len(trader.positions),
        },
        "telemetry": stats,
    })


async def handle_download_csv(request):
    """Download telemetry CSV file."""
    csv_path = telemetry.csv_path
    if not csv_path.exists():
        return web.Response(text="No telemetry data recorded yet.", status=404)
    return web.FileResponse(
        path=csv_path,
        headers={"Content-Disposition": 'attachment; filename="telemetry_opportunities.csv"'}
    )


async def handle_dashboard(request):
    """Render the sleek dark-mode HTML dashboard."""
    stats = telemetry.get_summary_stats()
    total_equity = trader.total_equity
    starting = PAPER_STARTING_BALANCE
    roi_pct = ((total_equity - starting) / starting) * 100

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Polymarket Bot Cloud Dashboard</title>
  <meta http-equiv="refresh" content="15">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: #0b0f19;
      color: #e2e8f0;
      padding: 20px;
    }}
    .container {{ max-width: 1100px; margin: 0 auto; }}
    header {{
      display: flex; justify-content: space-between; align-items: center;
      padding-bottom: 20px; border-bottom: 1px solid #1e293b; margin-bottom: 24px;
    }}
    h1 {{ font-size: 1.5rem; font-weight: 700; color: #f8fafc; display: flex; align-items: center; gap: 8px; }}
    .badge {{
      display: inline-flex; align-items: center; gap: 6px;
      background: rgba(16, 185, 129, 0.1); color: #10b981;
      padding: 4px 10px; border-radius: 9999px; font-size: 0.8rem; font-weight: 600;
    }}
    .dot {{ width: 8px; height: 8px; background: #10b981; border-radius: 50%; animation: pulse 2s infinite; }}
    @keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.3; }} }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }}
    .card {{
      background: #131b2e; border: 1px solid #1e293b; border-radius: 12px; padding: 18px;
    }}
    .card-title {{ font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; color: #94a3b8; margin-bottom: 6px; }}
    .card-val {{ font-size: 1.6rem; font-weight: 700; color: #f8fafc; }}
    .positive {{ color: #10b981; }}
    .negative {{ color: #f43f5e; }}
    .btn {{
      background: #2563eb; color: #fff; padding: 8px 16px; border-radius: 8px;
      text-decoration: none; font-size: 0.85rem; font-weight: 600; display: inline-block;
      transition: background 0.2s;
    }}
    .btn:hover {{ background: #1d4ed8; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 0.9rem; }}
    th {{ background: #1e293b; color: #94a3b8; text-align: left; padding: 10px; font-weight: 600; }}
    td {{ padding: 10px; border-bottom: 1px solid #1e293b; color: #cbd5e1; }}
    tr:hover td {{ background: #17223b; }}
    .section-title {{ font-size: 1.15rem; font-weight: 600; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }}
    footer {{ text-align: center; color: #64748b; font-size: 0.8rem; margin-top: 40px; }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1>⚡ Polymarket Cloud Bot</h1>
        <p style="color: #64748b; font-size: 0.85rem; margin-top: 4px;">Running 24/7 in Cloud Datacenter | Zero Local Data Consumed</p>
      </div>
      <div>
        <span class="badge"><span class="dot"></span> {loop_state['status']}</span>
        <a href="/download-telemetry" class="btn" style="margin-left: 12px;">📥 Download CSV</a>
      </div>
    </header>

    <div class="grid">
      <div class="card">
        <div class="card-title">Total Equity</div>
        <div class="card-val">${total_equity:,.2f}</div>
      </div>
      <div class="card">
        <div class="card-title">Cash Balance</div>
        <div class="card-val">${trader.balance:,.2f}</div>
      </div>
      <div class="card">
        <div class="card-title">Total ROI</div>
        <div class="card-val {'positive' if roi_pct >= 0 else 'negative'}">{roi_pct:+.2f}%</div>
      </div>
      <div class="card">
        <div class="card-title">Scanned Iterations</div>
        <div class="card-val">{loop_state['iterations']}</div>
      </div>
      <div class="card">
        <div class="card-title">Recorded Opportunities</div>
        <div class="card-val positive">{stats.get('total_records', 0)}</div>
      </div>
    </div>

    <div class="card" style="margin-bottom: 24px;">
      <div class="section-title">
        <span>📊 Live Captured Telemetry (Latest Opportunities)</span>
        <span style="font-size: 0.8rem; color: #64748b;">Auto-refreshes every 15s</span>
      </div>
      <div style="overflow-x: auto;">
        <table>
          <thead>
            <tr>
              <th>Time (UTC)</th>
              <th>Type</th>
              <th>Market Question</th>
              <th style="text-align: right;">Spread %</th>
              <th style="text-align: right;">Margin %</th>
              <th style="text-align: right;">Liquidity</th>
              <th style="text-align: right;">Depth ($)</th>
            </tr>
          </thead>
          <tbody>
            {"".join(f'''
            <tr>
              <td style="color: #94a3b8; font-size: 0.8rem;">{r.get('timestamp_iso', '')[11:19]}</td>
              <td><span style="color: {'#10b981' if r.get('type')=='LONG_ARBITRAGE' else '#38bdf8'}; font-weight: 600;">{r.get('type', '')}</span></td>
              <td style="max-width: 380px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{r.get('question', '')}</td>
              <td style="text-align: right; color: #10b981; font-weight: 600;">{float(r.get('spread_pct', 0)):.1f}%</td>
              <td style="text-align: right; color: #f43f5e; font-weight: 600;">{float(r.get('margin_pct', 0)):.1f}%</td>
              <td style="text-align: right;">${float(r.get('liquidity', 0)):,.0f}</td>
              <td style="text-align: right;">${float(r.get('available_depth_usdc', 0)):,.0f}</td>
            </tr>
            ''' for r in reversed(stats.get('recent_records', [])[-12:])) or '<tr><td colspan="7" style="text-align:center; padding: 20px; color: #64748b;">Scanning in progress... opportunities will appear shortly.</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>

    <footer>
      Polymarket Quantitative Cloud Bot &bull; Last Scan: {loop_state['last_scan_time']}
    </footer>
  </div>
</body>
</html>
"""
    return web.Response(text=html, content_type="text/html")


async def handle_ping(request):
    """Ultra-lightweight 4-byte ping for cron-job.org keep-alive."""
    return web.Response(text="pong", content_type="text/plain")


async def init_app():
    app = web.Application()
    app.router.add_get("/", handle_dashboard)
    app.router.add_get("/ping", handle_ping)
    app.router.add_get("/api/status", handle_status_api)
    app.router.add_get("/download-telemetry", handle_download_csv)

    # Start background scanner
    asyncio.create_task(background_scanner_loop())
    return app


if __name__ == "__main__":
    app = init_app()
    logger.info(f"Starting web server on http://0.0.0.0:{PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)
