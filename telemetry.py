"""
Opportunity & Market Telemetry Logger for Polymarket
Records detected anomalies, spreads, and tracks duration/decay to discover real alpha.
"""
import os
import csv
import time
import logging
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Optional, Any, List

logger = logging.getLogger("polymarket_bot.telemetry")

CSV_HEADER = [
    "timestamp_iso",
    "timestamp_unix",
    "market_id",
    "question",
    "type",
    "yes_ask",
    "no_ask",
    "sum_asks",
    "margin_pct",
    "spread_pct",
    "available_depth_usdc",
    "volume_24h",
    "liquidity",
    "lifespan_sec",
]


class TelemetryLogger:
    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path(__file__).resolve().parent
        self.csv_path = self.output_dir / "telemetry_opportunities.csv"
        self._active_opportunities: Dict[str, Dict[str, Any]] = {}
        self._init_csv()

    def _init_csv(self):
        if not self.csv_path.exists():
            with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(CSV_HEADER)

    def record_opportunity(
        self,
        market_id: str,
        question: str,
        opp_type: str,
        yes_ask: float,
        no_ask: float,
        sum_asks: float,
        margin_pct: float,
        spread_pct: float,
        available_depth_usdc: float,
        volume_24h: float,
        liquidity: float,
    ):
        """Record or update an ongoing opportunity to track its lifespan."""
        now = time.time()
        now_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        key = f"{market_id}_{opp_type}"

        if key in self._active_opportunities:
            # Opportunity is still open; update lifespan
            opp = self._active_opportunities[key]
            opp["last_seen"] = now
            opp["lifespan_sec"] = round(now - opp["first_seen"], 1)
        else:
            # New opportunity detected
            opp_data = {
                "timestamp_iso": now_iso,
                "timestamp_unix": now,
                "market_id": market_id,
                "question": question,
                "type": opp_type,
                "yes_ask": round(yes_ask, 4),
                "no_ask": round(no_ask, 4),
                "sum_asks": round(sum_asks, 4),
                "margin_pct": round(margin_pct, 2),
                "spread_pct": round(spread_pct, 2),
                "available_depth_usdc": round(available_depth_usdc, 2),
                "volume_24h": round(volume_24h, 2),
                "liquidity": round(liquidity, 2),
                "first_seen": now,
                "last_seen": now,
                "lifespan_sec": 0.0,
            }
            self._active_opportunities[key] = opp_data
            self._append_row(opp_data)
            logger.info(f"📊 Telemetry logged new opportunity: [{opp_type}] {question[:40]}... (Margin: {margin_pct:.1f}%)")

    def prune_expired(self, max_idle_sec: float = 30.0):
        """Remove opportunities that disappeared from the order book."""
        now = time.time()
        expired_keys = []
        for key, opp in self._active_opportunities.items():
            if now - opp["last_seen"] > max_idle_sec:
                expired_keys.append(key)

        for key in expired_keys:
            del self._active_opportunities[key]

    def _append_row(self, data: Dict[str, Any]):
        try:
            with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    data["timestamp_iso"],
                    data["timestamp_unix"],
                    data["market_id"],
                    data["question"],
                    data["type"],
                    data["yes_ask"],
                    data["no_ask"],
                    data["sum_asks"],
                    data["margin_pct"],
                    data["spread_pct"],
                    data["available_depth_usdc"],
                    data["volume_24h"],
                    data["liquidity"],
                    data["lifespan_sec"],
                ])
        except Exception as e:
            logger.error(f"Error appending to telemetry CSV: {e}")

    def get_summary_stats(self) -> Dict[str, Any]:
        """Parse CSV and calculate aggregated statistics."""
        if not self.csv_path.exists():
            return {"total_records": 0}

        records = []
        try:
            with open(self.csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    records.append(row)
        except Exception as e:
            logger.error(f"Error reading telemetry CSV: {e}")
            return {"error": str(e)}

        if not records:
            return {"total_records": 0}

        type_counts: Dict[str, int] = {}
        margins: List[float] = []
        spreads: List[float] = []

        for r in records:
            t = r.get("type", "UNKNOWN")
            type_counts[t] = type_counts.get(t, 0) + 1
            try:
                margins.append(float(r.get("margin_pct", 0)))
                spreads.append(float(r.get("spread_pct", 0)))
            except ValueError:
                continue

        avg_margin = sum(margins) / len(margins) if margins else 0.0
        max_margin = max(margins) if margins else 0.0
        avg_spread = sum(spreads) / len(spreads) if spreads else 0.0

        return {
            "total_records": len(records),
            "type_counts": type_counts,
            "avg_margin_pct": round(avg_margin, 2),
            "max_margin_pct": round(max_margin, 2),
            "avg_spread_pct": round(avg_spread, 2),
            "recent_records": records[-10:],
        }
