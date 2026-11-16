"""
Polymarket Bot CLI & Runner
Interactive dashboard for scanning markets, inspecting order books, and running paper trading strategies.
"""
import argparse
import sys
import time
import logging
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

from config import PAPER_STARTING_BALANCE
from market_scanner import MarketScanner
from orderbook import OrderBookFetcher
from paper_trader import PaperTrader
from strategies import ArbitrageHunter, MarketMaker
from telemetry import TelemetryLogger

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("polymarket_bot")
console = Console()


def cmd_scan(args):
    """Scan and list top active markets on Polymarket."""
    scanner = MarketScanner()
    console.print(f"\n[bold cyan]🔍 Fetching {args.limit} active markets from Polymarket Gamma API...[/bold cyan]")
    markets = scanner.fetch_markets(limit=args.limit, order=args.sort)

    if not markets:
        console.print("[red]No markets found or API unreachable.[/red]")
        return

    table = Table(title=f"Polymarket Active Markets (Top {len(markets)})", box=box.ROUNDED)
    table.add_column("ID", style="dim", width=8)
    table.add_column("Question", style="bold white", max_width=45)
    table.add_column("Outcomes & Prices", style="cyan")
    table.add_column("24h Vol", justify="right", style="green")
    table.add_column("Liquidity", justify="right", style="yellow")
    table.add_column("Sum", justify="right", style="magenta")

    for m in markets:
        outcomes_str = []
        for i, o in enumerate(m.outcomes):
            price_str = f"${m.outcome_prices[i]:.2f}" if i < len(m.outcome_prices) else "N/A"
            outcomes_str.append(f"{o}: {price_str}")
        outcomes_display = " | ".join(outcomes_str)

        sum_style = "bold green" if 0.90 <= m.price_sum <= 1.01 else "bold red"

        table.add_row(
            m.id[:8],
            m.question,
            outcomes_display,
            f"${m.volume_24h:,.0f}",
            f"${m.liquidity:,.0f}",
            Text(f"{m.price_sum:.2f}", style=sum_style),
        )

    console.print(table)


def cmd_arb(args):
    """Scan for binary arbitrage opportunities (Yes + No < 1.00)."""
    scanner = MarketScanner()
    fetcher = OrderBookFetcher()
    trader = PaperTrader()
    hunter = ArbitrageHunter(trader, fetcher)

    console.print(f"\n[bold yellow]⚡ Scanning {args.limit} markets for pricing anomalies & arbitrage...[/bold yellow]")
    markets = scanner.fetch_markets(limit=args.limit, order="volume24hr")

    candidates = scanner.find_arbitrage_candidates(markets, min_profit_pct=args.min_margin)

    if not candidates:
        console.print("[green]No obvious price-sum anomalies found at high-level feed. Checking top order books...[/green]")

    table = Table(title="Arbitrage Candidates & Price Sum Discrepancies", box=box.ROUNDED)
    table.add_column("Question", style="bold white", max_width=45)
    table.add_column("Type", style="cyan")
    table.add_column("Yes / No Price", style="white")
    table.add_column("Sum", justify="right", style="magenta")
    table.add_column("Margin %", justify="right", style="bold green")
    table.add_column("Action", style="yellow")

    executable_opps = []

    for c in candidates:
        m = c["market"]
        table.add_row(
            m.question,
            c["type"],
            f"${c['yes_price']:.2f} / ${c['no_price']:.2f}",
            f"${c['price_sum']:.2f}",
            f"{c['profit_margin_pct']:.2f}%",
            "Verifying Orderbook..." if c["type"] == "LONG_ARBITRAGE" else "Monitor",
        )

        if c["type"] == "LONG_ARBITRAGE":
            opp = hunter.evaluate_market(m)
            if opp:
                executable_opps.append(opp)

    console.print(table)

    if executable_opps:
        console.print(f"\n[bold green]🔥 Found {len(executable_opps)} EXECUTABLE live order book arbitrage opportunities![/bold green]")
        for opp in executable_opps:
            console.print(
                f"Market: [bold]{opp['market'].question}[/bold]\n"
                f"  Best Ask YES: ${opp['yes_ask']:.3f} | Best Ask NO: ${opp['no_ask']:.3f} | "
                f"Sum: [bold green]${opp['sum_asks']:.3f}[/bold green] (Gross Margin: {opp['margin_pct']:.2f}%)\n"
                f"  Max Available Size: {opp['max_shares']:.1f} shares (~${opp['max_cost_usdc']:.2f})"
            )
            if args.execute:
                console.print("[yellow]Simulating execution...[/yellow]")
                success = hunter.execute_arbitrage(opp, budget_usdc=args.budget)
                if success:
                    console.print("[bold green]✅ Simulated Arbitrage Executed Successfully![/bold green]")
    else:
        console.print("[dim]No instant orderbook-depth arbitrage at or above margin threshold right now.[/dim]")


def cmd_longtail(args):
    """Find long-tail markets with wide spreads for market making."""
    scanner = MarketScanner()
    fetcher = OrderBookFetcher()

    console.print(f"\n[bold cyan]🎯 Scanning for Long-Tail Market Making Candidates...[/bold cyan]")
    markets = scanner.fetch_markets(limit=args.limit, order="volume24hr")
    longtail_markets = scanner.find_long_tail_opportunities(
        markets,
        min_liquidity=args.min_liq,
        min_volume=args.min_vol,
        max_volume=args.max_vol,
    )

    table = Table(title=f"Long-Tail Market Making Targets ({len(longtail_markets)} Found)", box=box.ROUNDED)
    table.add_column("Question", style="bold white", max_width=45)
    table.add_column("Liquidity", justify="right", style="yellow")
    table.add_column("Total Vol", justify="right", style="green")
    table.add_column("Best Bid", justify="right", style="cyan")
    table.add_column("Best Ask", justify="right", style="magenta")
    table.add_column("Spread", justify="right", style="bold green")

    count = 0
    for m in longtail_markets[:args.show]:
        if not m.clob_token_ids:
            continue
        token_id = m.clob_token_ids[0]
        book = fetcher.fetch_order_book(token_id)
        if not book or not book.spread:
            continue

        spread_display = f"{book.spread_pct:.1f}% (${book.spread:.2f})" if book.spread_pct else "N/A"
        spread_style = "bold green" if book.spread >= 0.03 else "dim white"

        table.add_row(
            m.question,
            f"${m.liquidity:,.0f}",
            f"${m.total_volume:,.0f}",
            f"${book.best_bid:.2f}" if book.best_bid else "-",
            f"${book.best_ask:.2f}" if book.best_ask else "-",
            Text(spread_display, style=spread_style),
        )
        count += 1

    console.print(table)


def cmd_book(args):
    """Inspect full L2 order book for a given token ID."""
    fetcher = OrderBookFetcher()
    console.print(f"\n[bold cyan]📖 Fetching L2 Order Book for Token:[/bold cyan] {args.token_id}")
    book = fetcher.fetch_order_book(args.token_id)

    if not book:
        console.print("[red]Could not retrieve order book. Ensure token ID is valid.[/red]")
        return

    summary_panel = Panel(
        f"[bold]Best Bid:[/bold] ${book.best_bid if book.best_bid else 0.0:.3f} ({book.best_bid_size:.1f} shares)\n"
        f"[bold]Best Ask:[/bold] ${book.best_ask if book.best_ask else 0.0:.3f} ({book.best_ask_size:.1f} shares)\n"
        f"[bold]Midpoint:[/bold] ${book.midpoint if book.midpoint else 0.0:.3f}\n"
        f"[bold]Spread:[/bold] ${book.spread if book.spread else 0.0:.3f} ({book.spread_pct if book.spread_pct else 0.0:.2f}%)\n"
        f"[bold]Total Bid Depth:[/bold] ${book.total_bid_depth_usdc:,.2f} | [bold]Total Ask Depth:[/bold] ${book.total_ask_depth_usdc:,.2f}",
        title="Order Book Summary",
        box=box.ROUNDED,
    )
    console.print(summary_panel)

    table = Table(title="L2 Depth (Top 10 Bids & Asks)", box=box.SIMPLE)
    table.add_column("Bid Size", justify="right", style="cyan")
    table.add_column("Bid Price", justify="right", style="bold cyan")
    table.add_column("|", justify="center", style="dim")
    table.add_column("Ask Price", justify="right", style="bold magenta")
    table.add_column("Ask Size", justify="right", style="magenta")

    max_rows = min(10, max(len(book.bids), len(book.asks)))
    for i in range(max_rows):
        bid_str = f"{book.bids[i].size:,.1f}" if i < len(book.bids) else ""
        bid_px = f"${book.bids[i].price:.3f}" if i < len(book.bids) else ""
        ask_px = f"${book.asks[i].price:.3f}" if i < len(book.asks) else ""
        ask_str = f"{book.asks[i].size:,.1f}" if i < len(book.asks) else ""
        table.add_row(bid_str, bid_px, "|", ask_px, ask_str)

    console.print(table)


def cmd_portfolio(args):
    """View paper trading portfolio, balances, and positions."""
    trader = PaperTrader()
    fetcher = OrderBookFetcher()

    # Update current market prices for open positions
    price_map = {}
    for tid in trader.positions.keys():
        b = fetcher.fetch_order_book(tid)
        if b and b.midpoint:
            price_map[tid] = b.midpoint
    trader.update_market_prices(price_map)

    total_equity = trader.total_equity
    starting = PAPER_STARTING_BALANCE
    roi_pct = ((total_equity - starting) / starting) * 100

    panel = Panel(
        f"[bold]Cash Balance (USDC):[/bold] ${trader.balance:,.2f}\n"
        f"[bold]Total Equity:[/bold] ${total_equity:,.2f}\n"
        f"[bold]Realized PnL:[/bold] ${trader.total_realized_pnl:+,.2f}\n"
        f"[bold]Unrealized PnL:[/bold] ${trader.total_unrealized_pnl:+,.2f}\n"
        f"[bold]Total ROI:[/bold] {roi_pct:+.2f}%\n"
        f"[bold]Win / Loss Record:[/bold] {trader.wins} Wins / {trader.losses} Losses",
        title="💼 Paper Portfolio Summary",
        box=box.ROUNDED,
    )
    console.print(panel)

    if trader.positions:
        pos_table = Table(title="Open Positions", box=box.ROUNDED)
        pos_table.add_column("Question", style="bold white", max_width=40)
        pos_table.add_column("Side", style="cyan")
        pos_table.add_column("Shares", justify="right")
        pos_table.add_column("Avg Entry", justify="right")
        pos_table.add_column("Current", justify="right")
        pos_table.add_column("Cost Basis", justify="right")
        pos_table.add_column("Market Value", justify="right")
        pos_table.add_column("PnL ($ / %)", justify="right")

        for pos in trader.positions.values():
            pnl_style = "bold green" if pos.unrealized_pnl >= 0 else "bold red"
            pos_table.add_row(
                pos.question,
                pos.outcome,
                f"{pos.shares:,.1f}",
                f"${pos.avg_entry_price:.3f}",
                f"${pos.current_price:.3f}",
                f"${pos.cost_basis:,.2f}",
                f"${pos.market_value:,.2f}",
                Text(f"${pos.unrealized_pnl:+.2f} ({pos.pnl_pct:+.1f}%)", style=pnl_style),
            )
        console.print(pos_table)
    else:
        console.print("[dim]No active positions open.[/dim]")

    if trader.trade_history:
        console.print(f"\n[bold]Recent Simulated Trades ({len(trader.trade_history)} total):[/bold]")
        trade_table = Table(box=box.SIMPLE)
        trade_table.add_column("Trade ID", style="dim")
        trade_table.add_column("Time", style="dim")
        trade_table.add_column("Side", style="bold")
        trade_table.add_column("Outcome")
        trade_table.add_column("Shares", justify="right")
        trade_table.add_column("Avg Price", justify="right")
        trade_table.add_column("Cost / Val", justify="right")
        trade_table.add_column("Slippage", justify="right", style="yellow")

        for t in trader.trade_history[-5:]:
            tm = time.strftime("%H:%M:%S", time.localtime(t.timestamp))
            side_color = "green" if t.side == "BUY" else "magenta"
            trade_table.add_row(
                t.trade_id,
                tm,
                Text(t.side, style=side_color),
                t.outcome,
                f"{t.shares:,.1f}",
                f"${t.avg_price:.3f}",
                f"${t.cost_usdc:,.2f}",
                f"{t.slippage_pct:.2f}%",
            )
        console.print(trade_table)


def cmd_daemon(args):
    """Autonomous monitoring daemon: scans on interval, logs telemetry, executes paper arbs."""
    console.print(f"\n[bold green]🤖 Starting Polymarket Telemetry & Paper Trading Daemon...[/bold green]")
    console.print(f"Monitoring every [cyan]{args.interval}[/cyan]s | Min Arb Margin: [cyan]{args.min_margin*100}%[/cyan] | Min Spread: [cyan]{args.min_spread*100}%[/cyan]")
    console.print(f"Telemetry output: [yellow]telemetry_opportunities.csv[/yellow]")
    console.print("Press Ctrl+C to stop.\n")

    scanner = MarketScanner()
    fetcher = OrderBookFetcher()
    trader = PaperTrader()
    hunter = ArbitrageHunter(trader, fetcher)
    telemetry = TelemetryLogger()

    iteration = 0
    try:
        while True:
            iteration += 1
            now_str = time.strftime("%Y-%m-%d %H:%M:%S")
            console.print(f"[dim][{now_str}] Iteration #{iteration} scanning {args.limit} markets...[/dim]")

            markets = scanner.fetch_markets(limit=args.limit, order="volume24hr")

            # 1. Arbitrage Scanner & Telemetry
            candidates = scanner.find_arbitrage_candidates(markets, min_profit_pct=args.min_margin)
            for c in candidates:
                if c["type"] == "LONG_ARBITRAGE":
                    opp = hunter.evaluate_market(c["market"])
                    if opp:
                        console.print(f"[bold green]⚡ [ARB DETECTED] {opp['market'].question}[/bold green]")
                        console.print(f"  YES @ ${opp['yes_ask']:.3f} + NO @ ${opp['no_ask']:.3f} = ${opp['sum_asks']:.3f} (Margin: {opp['margin_pct']:.2f}%)")

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

                        if args.auto_trade:
                            hunter.execute_arbitrage(opp, budget_usdc=args.budget)

            # 2. Long-Tail Wide Spread Telemetry
            longtail_candidates = scanner.find_long_tail_opportunities(
                markets, min_liquidity=args.min_liq, min_volume=1000.0, max_volume=500000.0
            )
            for m in longtail_candidates[:12]:
                if not m.clob_token_ids:
                    continue
                book = fetcher.fetch_order_book(m.clob_token_ids[0])
                if book and book.spread_pct and book.spread_pct >= (args.min_spread * 100):
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

            # Clean expired opportunities
            telemetry.prune_expired(max_idle_sec=args.interval * 3)

            time.sleep(args.interval)
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Stopping daemon gracefully...[/bold yellow]")


def cmd_telemetry(args):
    """Display harvested telemetry report & captured opportunities."""
    telemetry = TelemetryLogger()
    stats = telemetry.get_summary_stats()

    if stats.get("total_records", 0) == 0:
        console.print("[yellow]No telemetry data recorded yet. Run the daemon to harvest opportunities.[/yellow]")
        return

    type_breakdown = ", ".join(f"{k}: {v}" for k, v in stats.get("type_counts", {}).items())
    panel = Panel(
        f"[bold]Total Recorded Opportunities:[/bold] {stats['total_records']}\n"
        f"[bold]Breakdown by Type:[/bold] {type_breakdown}\n"
        f"[bold]Average Spread:[/bold] {stats['avg_spread_pct']:.2f}%\n"
        f"[bold]Average Arbitrage Margin:[/bold] {stats['avg_margin_pct']:.2f}%\n"
        f"[bold]Max Anomaly Margin:[/bold] {stats['max_margin_pct']:.2f}%\n"
        f"[bold]Log File:[/bold] {telemetry.csv_path}",
        title="📊 Polymarket Telemetry & Opportunity Report",
        box=box.ROUNDED,
    )
    console.print(panel)

    recent = stats.get("recent_records", [])
    if recent:
        table = Table(title=f"Recent Captured Opportunities (Latest {len(recent)})", box=box.ROUNDED)
        table.add_column("Time", style="dim", width=19)
        table.add_column("Type", style="cyan")
        table.add_column("Question", style="bold white", max_width=42)
        table.add_column("Spread %", justify="right", style="green")
        table.add_column("Margin %", justify="right", style="magenta")
        table.add_column("Liquidity", justify="right", style="yellow")
        table.add_column("Depth ($)", justify="right")

        for r in recent:
            table.add_row(
                r.get("timestamp_iso", "")[:19],
                r.get("type", ""),
                r.get("question", ""),
                f"{float(r.get('spread_pct', 0)):.1f}%" if float(r.get("spread_pct", 0)) > 0 else "-",
                f"{float(r.get('margin_pct', 0)):.1f}%" if float(r.get("margin_pct", 0)) > 0 else "-",
                f"${float(r.get('liquidity', 0)):,.0f}",
                f"${float(r.get('available_depth_usdc', 0)):,.1f}",
            )
        console.print(table)


def cmd_trade(args):
    """Execute a simulated paper trade against real order book depth."""
    fetcher = OrderBookFetcher()
    trader = PaperTrader()

    console.print(f"\n[bold cyan]⚡ Executing Simulated {args.side.upper()} Trade on Token:[/bold cyan] {args.token_id}")
    book = fetcher.fetch_order_book(args.token_id)

    if not book:
        console.print("[red]Could not retrieve order book. Check token ID.[/red]")
        return

    if args.side.lower() == "buy":
        trade = trader.execute_buy(
            token_id=args.token_id,
            condition_id="manual",
            question=args.label or f"Token {args.token_id[:12]}...",
            outcome="Yes",
            target_amount_usdc=args.amount,
            book=book,
        )
        if trade:
            console.print(f"[bold green]✅ Simulated BUY Executed Successfully![/bold green]")
            console.print(
                f"Shares: [bold]{trade.shares:,.2f}[/bold] | Avg Price: [bold]${trade.avg_price:.3f}[/bold] | "
                f"Cost: [bold]${trade.cost_usdc:.2f}[/bold] | Slippage: [yellow]{trade.slippage_pct:.2f}%[/yellow]"
            )
        else:
            console.print("[red]Trade failed. Check balance or order book liquidity.[/red]")
    elif args.side.lower() == "sell":
        trade = trader.execute_sell(
            token_id=args.token_id,
            shares_to_sell=args.shares,
            book=book,
        )
        if trade:
            console.print(f"[bold green]✅ Simulated SELL Executed Successfully![/bold green]")
            console.print(
                f"Shares Sold: [bold]{trade.shares:,.2f}[/bold] | Avg Price: [bold]${trade.avg_price:.3f}[/bold] | "
                f"Proceeds: [bold]${trade.cost_usdc:.2f}[/bold] | Realized PnL: [bold green]${trade.realized_pnl:+.2f}[/bold green]"
            )
        else:
            console.print("[red]Trade failed. Ensure you hold open shares in this token.[/red]")


def main():
    parser = argparse.ArgumentParser(description="Polymarket Trading Bot & Analysis Suite")
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to run")

    # scan
    p_scan = subparsers.add_parser("scan", help="Scan active markets on Polymarket")
    p_scan.add_argument("--limit", type=int, default=15, help="Number of markets to fetch")
    p_scan.add_argument("--sort", type=str, default="volume24hr", help="Sort field (volume24hr, liquidity, volume)")

    # arb
    p_arb = subparsers.add_parser("arb", help="Scan for arbitrage & price sum anomalies")
    p_arb.add_argument("--limit", type=int, default=30, help="Markets to analyze")
    p_arb.add_argument("--min-margin", type=float, default=0.01, help="Minimum profit margin (0.01 = 1%)")
    p_arb.add_argument("--execute", action="store_true", help="Execute simulated trade if opportunity exists")
    p_arb.add_argument("--budget", type=float, default=50.0, help="Simulated USDC budget per arb")

    # longtail
    p_lt = subparsers.add_parser("longtail", help="Find long-tail markets with wide spreads")
    p_lt.add_argument("--limit", type=int, default=50, help="Markets to fetch")
    p_lt.add_argument("--min-liq", type=float, default=3000.0, help="Minimum liquidity in USDC")
    p_lt.add_argument("--min-vol", type=float, default=1000.0, help="Minimum total volume")
    p_lt.add_argument("--max-vol", type=float, default=500000.0, help="Maximum total volume")
    p_lt.add_argument("--show", type=int, default=15, help="Number of targets to display")

    # book
    p_book = subparsers.add_parser("book", help="Inspect L2 order book for a token ID")
    p_book.add_argument("token_id", type=str, help="Polymarket CLOB token ID")

    # trade
    p_trade = subparsers.add_parser("trade", help="Simulate a paper buy/sell against real CLOB depth")
    p_trade.add_argument("token_id", type=str, help="Token ID to trade")
    p_trade.add_argument("--side", type=str, choices=["buy", "sell"], default="buy", help="Order side")
    p_trade.add_argument("--amount", type=float, default=25.0, help="Amount in USDC for buy orders")
    p_trade.add_argument("--shares", type=float, default=10.0, help="Number of shares for sell orders")
    p_trade.add_argument("--label", type=str, default="", help="Custom label/question for the position")

    # portfolio
    p_port = subparsers.add_parser("portfolio", help="View simulated paper trading portfolio")

    # daemon
    p_daemon = subparsers.add_parser("daemon", help="Run automated paper trading background daemon")
    p_daemon.add_argument("--interval", type=int, default=15, help="Loop interval in seconds")
    p_daemon.add_argument("--limit", type=int, default=40, help="Markets to scan per loop")
    p_daemon.add_argument("--min-margin", type=float, default=0.01, help="Min arb profit margin")
    p_daemon.add_argument("--min-spread", type=float, default=0.03, help="Min spread threshold (0.03 = 3%)")
    p_daemon.add_argument("--min-liq", type=float, default=3000.0, help="Min liquidity in USDC")
    p_daemon.add_argument("--budget", type=float, default=50.0, help="Budget per arb trade")
    p_daemon.add_argument("--auto-trade", action="store_true", help="Automatically execute simulated paper trades")

    # telemetry
    p_telem = subparsers.add_parser("telemetry", help="Display harvested telemetry report & captured opportunities")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmd_map = {
        "scan": cmd_scan,
        "arb": cmd_arb,
        "longtail": cmd_longtail,
        "book": cmd_book,
        "trade": cmd_trade,
        "portfolio": cmd_portfolio,
        "daemon": cmd_daemon,
        "telemetry": cmd_telemetry,
    }

    cmd_map[args.command](args)


if __name__ == "__main__":
    main()
