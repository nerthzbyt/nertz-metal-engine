"""
tsm_bridge.py
=============

Bridge to the TSM Exchange Formulas pipeline (QuestDB + Postgres + live Bybit feed).

Loads real market snapshots from Postgres L2 / QuestDB trades, runs
``compute_indicators()`` from tsm_exchange_formulas, and maps results into
the NertzMetalEngine metrics dict used by formulas.py and nertz.py.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("TsmBridge")

DEFAULT_TSM_PATH = r"D:\documentos\trae_projects\Tsm_exanges_formulas"

CORE_INDICATOR_KEYS = (
    "ild", "egm", "rol", "pio", "ogm", "combined",
    "spot_pressure_fusion", "spot_liquidity_combo", "adm_v2", "cms", "mhbf",
    "obi", "tfi", "dac", "spread", "microprice", "volatility",
    "basis", "wsr", "lpi", "amf", "le", "ffd", "orderbook_pressure",
    "rvol", "oi_delta_pct",
)


def _tsm_config():
    from .parameters import Config
    return Config.TSM


def is_tsm_enabled() -> bool:
    return _tsm_config().enabled


def get_tsm_path() -> str:
    return os.getenv("TSM_FORMULAS_PATH", DEFAULT_TSM_PATH)


def ensure_tsm_path() -> str:
    path = get_tsm_path()
    if path not in sys.path:
        sys.path.insert(0, path)
    return path


def tsm_compute_params() -> Dict[str, float]:
    """Weights from parameters.Config.TSM — single source of truth."""
    return _tsm_config().as_compute_kwargs()


def extract_indicator_value(node: Any) -> Optional[float]:
    if isinstance(node, (int, float)):
        return float(node)
    if isinstance(node, dict):
        val = node.get("value")
        if isinstance(val, (int, float)):
            return float(val)
    return None


def map_tsm_result(tsm_result: Dict[str, Any]) -> Dict[str, float]:
    """Map TSM ``compute_indicators`` output to flat nertz metrics."""
    out: Dict[str, float] = {"tsm_enriched": 1.0}

    for key in CORE_INDICATOR_KEYS:
        val = extract_indicator_value(tsm_result.get(key))
        if val is not None:
            out[key] = val

    raw_metrics = tsm_result.get("metrics") or {}
    for key in ("best_bid", "best_ask", "mid_price", "volume_24h", "turnover_24h"):
        raw = raw_metrics.get(key)
        if raw is not None:
            try:
                out[key] = float(raw)
            except (TypeError, ValueError):
                pass

    winner = (tsm_result.get("signals") or {}).get("winner") or {}
    if isinstance(winner, dict) and winner.get("value") is not None:
        try:
            out["tsm_winner_value"] = float(winner["value"])
        except (TypeError, ValueError):
            pass

    return out


def _normalize_book_side(rows: List[Any]) -> List[List[str]]:
    cleaned: List[List[str]] = []
    for item in rows or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            cleaned.append([str(item[0]), str(item[1])])
    return cleaned


def candles_to_synthetic_trades(
    candles: List[Dict[str, Any]],
    *,
    symbol: str = "BTCUSDT",
) -> List[Dict[str, Any]]:
    """
    TSM volatility/momentum need trade ticks. When live trades are sparse,
    synthesize minimal ticks from OHLCV so ild/pio/ogm/volatility can compute.
    """
    trades: List[Dict[str, Any]] = []
    base_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    for i, c in enumerate(candles[-40:]):
        ts = int(c.get("timestamp") or (base_ms - (40 - i) * 60_000))
        vol = float(c.get("volume") or 0.0)
        q = max(vol / 4.0, 1e-6)
        for j, px in enumerate(
            (c.get("open"), c.get("high"), c.get("low"), c.get("close"))
        ):
            if px is None:
                continue
            trades.append({
                "T": ts + j,
                "p": str(px),
                "q": str(q),
                "S": "Buy" if j % 2 == 0 else "Sell",
                "s": symbol,
            })
    return trades


def build_live_snapshot(
    symbol: str,
    candles: List[Dict[str, Any]],
    orderbook: Dict[str, Any],
    ticker: Dict[str, Any],
    *,
    recent_trades: Optional[List[Dict[str, Any]]] = None,
    venue: str = "bybit_v5",
) -> Dict[str, Any]:
    """Build TSM-compatible snapshot from live Bybit websocket/REST data."""
    bids = _normalize_book_side(orderbook.get("bids", []))
    asks = _normalize_book_side(orderbook.get("asks", []))
    last_price = float(ticker.get("last_price") or ticker.get("lastPrice") or 0.0)
    vol24 = float(
        ticker.get("volume_24h")
        or ticker.get("volume24h")
        or ticker.get("volume")
        or 0.0
    )
    high24 = float(ticker.get("high_24h") or ticker.get("highPrice24h") or last_price)
    low24 = float(ticker.get("low_24h") or ticker.get("lowPrice24h") or last_price)
    ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    trades = list(recent_trades or [])
    if len(trades) < 20:
        trades = candles_to_synthetic_trades(candles, symbol=symbol) + trades

    return {
        "venue": venue,
        "symbol": symbol,
        "order_book": {
            "bids": bids[:80],
            "asks": asks[:80],
            "E": ts_ms,
        },
        "premium_index": {
            "markPrice": str(last_price),
            "indexPrice": str(last_price),
            "lastFundingRate": "0",
            "nextFundingTime": str(ts_ms + 28_800_000),
        },
        "open_interest": {"openInterest": str(ticker.get("open_interest", "0"))},
        "open_interest_hist": [],
        "ticker_24h": {
            "lastPrice": str(last_price),
            "volume24h": str(vol24),
            "turnover24h": str(vol24 * last_price),
            "highPrice24h": str(high24),
            "lowPrice24h": str(low24),
            "markPrice": str(last_price),
            "indexPrice": str(last_price),
        },
        "global_long_short_ratio": [],
        "taker_buy_sell_vol": [],
        "trades": trades[-2000:],
        "liquidations": [],
        "instruments_info": {},
        "_source": "live",
    }


def fetch_questdb_recent_trades(symbol: str, window_s: int = 120) -> List[Dict[str, Any]]:
    """Optional: enrich live snapshot with real QuestDB market trades."""
    if not _tsm_config().questdb_trades:
        return []
    try:
        ensure_tsm_path()
        from tsm_exchange_formulas.tsm.questdb_loader import QuestDBClient, QuestDBConfig

        host = os.getenv("QUESTDB_HOST", "localhost")
        port = int(os.getenv("QUESTDB_HTTP_PORT", "9000"))
        client = QuestDBClient(QuestDBConfig(host=host, http_port=port))
        if not client.ping():
            return []

        end = datetime.now(timezone.utc)
        start = end - timedelta(seconds=window_s)
        sql = (
            f"SELECT timestamp, side, price, qty FROM trades "
            f"WHERE symbol = '{symbol}' "
            f"AND timestamp >= '{start.strftime('%Y-%m-%dT%H:%M:%S')}' "
            f"ORDER BY timestamp DESC LIMIT 500"
        )
        rows = client.query_dicts(sql)
        out = []
        for r in rows:
            ts_raw = r.get("timestamp")
            ts_ms = int(datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")).timestamp() * 1000)
            side = str(r.get("side", "Buy"))
            out.append({
                "T": ts_ms,
                "p": str(r.get("price")),
                "q": str(r.get("qty")),
                "S": "Buy" if side.lower().startswith("b") else "Sell",
                "s": symbol,
            })
        return out
    except Exception as exc:
        logger.debug("QuestDB trades fetch skipped: %s", exc)
        return []


def load_latest_snapshot_from_postgres(symbol: str = "BTCUSDT") -> Optional[Dict[str, Any]]:
    """Latest L2 orderbook snapshot from Postgres (port 5433)."""
    try:
        ensure_tsm_path()
        from tsm_exchange_formulas.tsm.postgres_market_loader import (
            PostgresLoaderConfig,
            PostgresMarketLoader,
        )

        cfg = PostgresLoaderConfig(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", "5433")),
            dbname=os.getenv("POSTGRES_DB", "trading"),
            user=os.getenv("POSTGRES_USER", "postgres"),
            password=os.getenv("POSTGRES_PASSWORD", "postgres"),
        )
        loader = PostgresMarketLoader(cfg)
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=2)
        snaps = loader.build_snapshots(
            symbol=symbol,
            start=start.strftime("%Y-%m-%d"),
            end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
            max_snaps=1,
            attach_system_metrics=False,
            use_system_indicators=False,
        )
        loader.close()
        if snaps:
            snaps[-1]["_source"] = "postgres"
            return snaps[-1]
    except Exception as exc:
        logger.warning("Postgres snapshot load failed: %s", exc)
    return None


def load_latest_snapshot_from_questdb(symbol: str = "BTCUSDT") -> Optional[Dict[str, Any]]:
    """Latest BBO + trades snapshot from QuestDB (port 9000)."""
    try:
        ensure_tsm_path()
        from tsm_exchange_formulas.tsm.questdb_loader import QuestDBConfig, QuestDBMarketLoader

        loader = QuestDBMarketLoader(
            QuestDBConfig(
                host=os.getenv("QUESTDB_HOST", "localhost"),
                http_port=int(os.getenv("QUESTDB_HTTP_PORT", "9000")),
            )
        )
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=2)
        snaps = loader.build_snapshots(
            symbol=symbol,
            start=start.strftime("%Y-%m-%d"),
            end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
            max_snaps=1,
            attach_nertzh=False,
        )
        if snaps:
            snaps[-1]["_source"] = "questdb"
            return snaps[-1]
    except Exception as exc:
        logger.warning("QuestDB snapshot load failed: %s", exc)
    return None


def load_snapshot_from_sqlite(
    db_path: str,
    symbol: str = "BTCUSDT",
) -> Optional[Dict[str, Any]]:
    """Fallback: SQLite trading.db (legacy NrTz/xxxx local store)."""
    if not os.path.exists(db_path):
        return None

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(
        "SELECT timestamp, open, high, low, close, volume FROM market_data "
        "WHERE symbol=? ORDER BY timestamp DESC LIMIT 50",
        (symbol,),
    )
    rows = cur.fetchall()
    candles = [
        {
            "timestamp": r[0],
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]),
        }
        for r in reversed(rows)
    ]

    cur.execute(
        "SELECT bids, asks FROM orderbook WHERE symbol=? ORDER BY timestamp DESC LIMIT 1",
        (symbol,),
    )
    ob_row = cur.fetchone()

    def _parse_side(raw: Any) -> List[List[str]]:
        if not raw:
            return []
        try:
            data = json.loads(raw)
            if isinstance(data, str):
                data = json.loads(data)
            return _normalize_book_side(data if isinstance(data, list) else [])
        except Exception:
            return []

    bids = _parse_side(ob_row[0]) if ob_row else []
    asks = _parse_side(ob_row[1]) if ob_row else []

    cur.execute(
        "SELECT last_price, volume_24h, high_24h, low_24h FROM market_ticker "
        "WHERE symbol=? ORDER BY timestamp DESC LIMIT 1",
        (symbol,),
    )
    tk_row = cur.fetchone() or (0.0, 0.0, 0.0, 0.0)
    conn.close()

    if not bids and not asks:
        return None

    ticker = {
        "last_price": float(tk_row[0]),
        "volume_24h": float(tk_row[1]),
        "high_24h": float(tk_row[2]),
        "low_24h": float(tk_row[3]),
    }
    snap = build_live_snapshot(symbol, candles, {"bids": bids, "asks": asks}, ticker)
    snap["_source"] = "sqlite"
    return snap


def load_snapshot_for_pipeline(
    symbol: str = "BTCUSDT",
    *,
    prefer: Optional[str] = None,
    sqlite_path: str = "data/trading.db",
) -> tuple[Optional[Dict[str, Any]], str]:
    """
    Load best available snapshot for offline pipeline runs.

    Priority: postgres (L2) → questdb (trades) → sqlite.
    """
    order = (prefer or os.getenv("TSM_DATA_SOURCE", "postgres,questdb,sqlite")).split(",")
    loaders = {
        "postgres": lambda: load_latest_snapshot_from_postgres(symbol),
        "questdb": lambda: load_latest_snapshot_from_questdb(symbol),
        "sqlite": lambda: load_snapshot_from_sqlite(sqlite_path, symbol),
    }
    for name in order:
        name = name.strip().lower()
        fn = loaders.get(name)
        if not fn:
            continue
        snap = fn()
        if snap:
            return snap, name
    return None, "none"


def compute_tsm_indicators(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Run full TSM indicator pipeline on a snapshot dict."""
    ensure_tsm_path()
    from tsm_exchange_formulas.exchange.indicators import compute_indicators

    return compute_indicators(snapshot, **tsm_compute_params())


def merge_metrics(base: Dict[str, Any], tsm_flat: Dict[str, float]) -> Dict[str, Any]:
    """Merge TSM values over simple formulas; keep base fallbacks for missing TSM keys."""
    merged = dict(base)
    core = ("ild", "egm", "rol", "pio", "ogm", "volatility", "combined")
    for key, val in tsm_flat.items():
        if key.startswith("tsm_"):
            merged[key] = val
            continue
        if val is None:
            continue
        if key in core and base.get(key) is not None and key not in tsm_flat:
            continue
        merged[key] = val

    if tsm_flat.get("spot_pressure_fusion") is not None:
        merged["spot_pressure_fusion"] = tsm_flat["spot_pressure_fusion"]
    if tsm_flat.get("combined") is not None:
        merged["combined"] = tsm_flat["combined"]
    if tsm_flat.get("obi") is not None:
        merged["obi"] = tsm_flat["obi"]
    if tsm_flat.get("tfi") is not None:
        merged["tfi"] = tsm_flat["tfi"]

    merged["metrics_source"] = "tsm+simple"
    return merged


def enrich_metrics(
    candle_data: List[Dict[str, Any]],
    orderbook_data: Dict[str, Any],
    ticker_data: Dict[str, Any],
    *,
    base_metrics: Optional[Dict[str, Any]] = None,
    symbol: str = "BTCUSDT",
    recent_trades: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Main live entry point: run TSM pipeline on current market state.

    Falls back to ``base_metrics`` when TSM is disabled or unavailable.
    """
    if not is_tsm_enabled():
        return base_metrics or {}

    try:
        trades = list(recent_trades or [])
        trades.extend(fetch_questdb_recent_trades(symbol))
        snapshot = build_live_snapshot(
            symbol, candle_data, orderbook_data, ticker_data, recent_trades=trades
        )
        tsm_result = compute_tsm_indicators(snapshot)
        tsm_flat = map_tsm_result(tsm_result)
        if not tsm_flat or tsm_flat.get("tsm_enriched") is None:
            return base_metrics or {}
        return merge_metrics(base_metrics or {}, tsm_flat)
    except Exception as exc:
        logger.warning("TSM enrich failed, using base metrics: %s", exc)
        return base_metrics or {}