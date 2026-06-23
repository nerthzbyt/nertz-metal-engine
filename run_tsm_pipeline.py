#!/usr/bin/env python3
"""
Run the full TSM Exchange indicators pipeline against real market data.

Data source priority (configurable via TSM_DATA_SOURCE):
  1. PostgreSQL :5433 — full L2 orderbook (50 levels), best for OBI/DAC/ROL
  2. QuestDB    :9000 — BBO + market trades, best for TFI/AMF/volatility
  3. SQLite     data/trading.db — local fallback

Output: tsm_full_output.json + console summary for NertzMetalEngine integration.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scripts.tsm_bridge import (
    compute_tsm_indicators,
    load_snapshot_for_pipeline,
    map_tsm_result,
    tsm_compute_params,
)


def main() -> int:
    symbol = os.getenv("SYMBOL", "BTCUSDT")
    sqlite_path = os.getenv("SQLITE_DB_PATH", "data/trading.db")
    prefer = os.getenv("TSM_DATA_SOURCE")

    print("=" * 60)
    print("TSM EXCHANGE INDICATORS PIPELINE — NertzMetalEngine")
    print("=" * 60)

    snapshot, source = load_snapshot_for_pipeline(
        symbol=symbol,
        prefer=prefer,
        sqlite_path=sqlite_path,
    )

    if not snapshot:
        print("ERROR: No snapshot loaded from postgres, questdb, or sqlite.")
        return 1

    ob = snapshot.get("order_book") or {}
    bids = ob.get("bids") or []
    asks = ob.get("asks") or []
    trades_n = len(snapshot.get("trades") or [])

    print(f"Source:     {source}")
    print(f"Symbol:     {symbol}")
    print(f"Orderbook:  bids={len(bids)} asks={len(asks)} L2={ob.get('_l2', len(bids) >= 20)}")
    print(f"Trades:     {trades_n}")
    print(f"Params:     {tsm_compute_params()}")

    result = compute_tsm_indicators(snapshot)
    flat = map_tsm_result(result)

    print("\n=== TSM INDICATOR OUTPUT ===")
    for key in (
        "ild", "egm", "rol", "pio", "ogm", "combined",
        "spot_pressure_fusion", "obi", "tfi", "dac", "volatility",
        "microprice", "spread",
    ):
        val = flat.get(key)
        if val is not None:
            print(f"  {key:22s}: {val:.6f}")

    winner = (result.get("signals") or {}).get("winner")
    if winner:
        print(f"\n  TSM winner signal: {winner.get('key')} level={winner.get('level')} value={winner.get('value')}")

    out_path = os.getenv("TSM_OUTPUT_PATH", "tsm_full_output.json")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "data_source": source,
        "params": tsm_compute_params(),
        "flat_metrics": flat,
        "full_result": result,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)

    print(f"\nFull output saved to {out_path}")
    print("\n=== INTEGRATION ===")
    print("Live bot uses scripts/tsm_bridge.py automatically when TSM_ENABLED=true.")
    print("Set TSM_DATA_SOURCE=postgres for L2-rich snapshots in offline runs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())