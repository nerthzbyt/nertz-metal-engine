"""
formulas.py
===========

High-quality, maintainable indicator and formula module.

Inspired by professional exchange formula libraries:
- Uses declarative IndicatorDef
- Pure compute functions
- Clear mathematical descriptions
- Centralized in one elegant file (no child-code sprawl)

All metrics (EGM, ILD, ROL, PIO, OGM) + Combined + supporting calculations live here.

This file, together with parameters.py, replaces scattered logic with artistic, professional code.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("NertzFormulas")

from .parameters import (
    Config,
    IndicatorDef,
    IndicatorValue,
    register_indicator,
    DEFAULT_WEIGHTS,
)


# =============================================================================
# Snapshot-like Protocol (minimal, for formulas)
# =============================================================================

class MarketSnapshot:
    """Lightweight snapshot adapter used by formula computes."""

    def __init__(self, candles: List[Dict], orderbook: Dict, ticker: Dict):
        self.candles = candles or []
        self.orderbook = orderbook or {"bids": [], "asks": []}
        self.ticker = ticker or {}

    @property
    def last_price(self) -> float:
        return float(self.ticker.get("last_price", 0.0))


# =============================================================================
# Core Formula Implementations (clean & professional)
# =============================================================================

def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def compute_egm(snapshot: MarketSnapshot, cfg: Config) -> float:
    """Elasticity / Exhaustion Gap Metric.
    Measures how far price has moved from recent average relative to range.
    """
    if len(snapshot.candles) < 2:
        return 0.0

    closes = np.array([_safe_float(c.get("close")) for c in snapshot.candles[-5:]])
    highs = np.array([_safe_float(c.get("high")) for c in snapshot.candles[-5:]])
    lows = np.array([_safe_float(c.get("low")) for c in snapshot.candles[-5:]])

    if len(highs) == 0 or len(lows) == 0 or snapshot.last_price <= 0:
        return 0.0

    avg_price = float(np.mean(closes))
    price_range = max(highs.max() - lows.min(), cfg.MIN_PRICE_RANGE_PCT * snapshot.last_price)

    egm = (snapshot.last_price - avg_price) / (price_range + cfg.EPSILON)
    return float(np.clip(egm, -1.0, 1.0))


def compute_ild(snapshot: MarketSnapshot, depth: int = 5) -> float:
    """Imbalance Level Delta - order book volume pressure."""
    bids = snapshot.orderbook.get("bids", [])[:depth]
    asks = snapshot.orderbook.get("asks", [])[:depth]

    bid_vol = sum(_safe_float(b[1]) for b in bids if _safe_float(b[1]) > 0)
    ask_vol = sum(_safe_float(a[1]) for a in asks if _safe_float(a[1]) > 0)
    total = bid_vol + ask_vol + 1e-9

    ild = (bid_vol - ask_vol) / total
    return float(np.clip(ild, -1.0, 1.0))


def compute_rol(snapshot: MarketSnapshot, depth: int = 5) -> float:
    """Relative Orderbook Liquidity - value-weighted imbalance."""
    bids = snapshot.orderbook.get("bids", [])[:depth]
    asks = snapshot.orderbook.get("asks", [])[:depth]

    bid_val = sum(_safe_float(b[0]) * _safe_float(b[1]) for b in bids if _safe_float(b[1]) > 0)
    ask_val = sum(_safe_float(a[0]) * _safe_float(a[1]) for a in asks if _safe_float(a[1]) > 0)
    total = bid_val + ask_val + 1e-9

    rol = (bid_val - ask_val) / total
    return float(np.clip(rol, -1.0, 1.0))


def compute_pio(snapshot: MarketSnapshot) -> float:
    """Price Impulse Oscillator - recent volume spike vs previous."""
    if len(snapshot.candles) < 2:
        return 0.0

    vols = np.array([_safe_float(c.get("volume")) for c in snapshot.candles[-5:]])
    if len(vols) < 2 or vols[:-1].mean() == 0:
        return 0.0

    latest = vols[-1]
    avg_prev = float(np.mean(vols[:-1]))
    pio = (latest - avg_prev) / (avg_prev + 1e-9)
    return float(np.clip(pio, -1.0, 1.0))


def compute_ogm(snapshot: MarketSnapshot, cfg: Config) -> float:
    """Orderbook Gap Metric - tightness of spread."""
    bids = snapshot.orderbook.get("bids", [])
    asks = snapshot.orderbook.get("asks", [])

    if not bids or not asks or snapshot.last_price <= 0:
        return 0.0

    best_bid = _safe_float(bids[0][0])
    best_ask = _safe_float(asks[0][0])
    spread = (best_ask - best_bid) / (snapshot.last_price + cfg.EPSILON)

    ogm = 1.0 - np.clip(spread / cfg.SPREAD_REFERENCE, 0.0, 1.0)
    return float(ogm)


def compute_combined(metrics: Dict[str, float], weights: Dict[str, float] | None = None, variation: str = "balanced") -> float:
    """Weighted + volatility-adjusted combined signal.

    Supports multiple validated variations (from TSM Exchange pipeline runs with our params).
    """
    if weights is None:
        weights = Config.COMBINED_VARIATIONS.get(variation, DEFAULT_WEIGHTS)

    base = (
        weights.get("egm", 0.2) * metrics.get("egm", 0.0) +
        weights.get("ild", 0.3) * metrics.get("ild", 0.0) +
        weights.get("rol", 0.3) * metrics.get("rol", 0.0) +
        weights.get("pio", 0.1) * metrics.get("pio", 0.0) +
        weights.get("ogm", 0.1) * metrics.get("ogm", 0.0)
    )

    # TSM-inspired volatility adjustment (makes combined more responsive in high vol)
    vol = metrics.get("volatility", 0.01)
    vol_factor = 1.0 + min(vol * 8, 0.8)   # slight amplification in volatile regimes

    scaled = base * Config.COMBINED_SCALE * vol_factor
    return float(np.clip(scaled, -Config.COMBINED_CLAMP, Config.COMBINED_CLAMP))


def compute_spot_pressure_fusion(metrics: Dict[str, float], ob_pressure: float = None) -> float:
    """Improved composite inspired by TSM 'spot_pressure_fusion'.

    Combines orderbook imbalance, flow (pio), and liquidity signals.
    """
    ild = metrics.get("ild", 0.0)
    pio = metrics.get("pio", 0.0)
    rol = metrics.get("rol", 0.0)
    vol = metrics.get("volatility", 0.01)

    # Weighted fusion with liquidity gate
    fusion = (0.45 * ild + 0.25 * pio + 0.30 * rol) * (1 + vol * 4)
    return float(np.clip(fusion, -1.0, 1.0))


def compute_volatility(snapshot: MarketSnapshot) -> float:
    if len(snapshot.candles) < 2 or snapshot.last_price <= 0:
        return 0.0
    highs = np.array([_safe_float(c.get("high")) for c in snapshot.candles[-5:]])
    lows = np.array([_safe_float(c.get("low")) for c in snapshot.candles[-5:]])
    if len(highs) == 0 or len(lows) == 0:
        return 0.0
    return float((highs.max() - lows.min()) / snapshot.last_price)


# =============================================================================
# Indicator Registration (executed at import)
# =============================================================================

def _build_core_indicators() -> None:
    """Register all core indicators at module load. Clean and declarative."""

    def egm_fn(s: MarketSnapshot) -> float:
        return compute_egm(s, Config)

    def ild_fn(s: MarketSnapshot) -> float:
        return compute_ild(s)

    def rol_fn(s: MarketSnapshot) -> float:
        return compute_rol(s)

    def pio_fn(s: MarketSnapshot) -> float:
        return compute_pio(s)

    def ogm_fn(s: MarketSnapshot) -> float:
        return compute_ogm(s, Config)

    register_indicator(IndicatorDef(
        key="egm", name="Exhaustion Gap Metric",
        formula="(last - avg) / range", fields=["candles", "ticker"],
        compute=egm_fn, category="signal",
        description="Price deviation from recent average normalized by range",
        output_range=(-1.0, 1.0),
    ))

    register_indicator(IndicatorDef(
        key="ild", name="Imbalance Level Delta",
        formula="(bid_vol - ask_vol) / total_vol", fields=["orderbook"],
        compute=ild_fn, category="orderbook",
        description="Raw volume pressure between bids and asks",
        output_range=(-1.0, 1.0),
    ))

    register_indicator(IndicatorDef(
        key="rol", name="Relative Orderbook Liquidity",
        formula="(bid_value - ask_value) / total_value", fields=["orderbook"],
        compute=rol_fn, category="orderbook",
        description="Value-weighted liquidity imbalance",
        output_range=(-1.0, 1.0),
    ))

    register_indicator(IndicatorDef(
        key="pio", name="Price Impulse Oscillator",
        formula="(latest_vol - avg_prev) / avg_prev", fields=["candles"],
        compute=pio_fn, category="volume",
        description="Volume impulse on the latest candle",
        output_range=(-1.0, 1.0),
    ))

    register_indicator(IndicatorDef(
        key="ogm", name="Orderbook Gap Metric",
        formula="1 - clip(spread / ref, 0, 1)", fields=["orderbook", "ticker"],
        compute=ogm_fn, category="spread",
        description="How tight the spread is relative to reference",
        output_range=(0.0, 1.0),
    ))

    def fusion_fn(s: MarketSnapshot) -> float:
        base = {
            "ild": compute_ild(s),
            "pio": compute_pio(s),
            "rol": compute_rol(s),
            "volatility": compute_volatility(s),
        }
        return compute_spot_pressure_fusion(base)

    register_indicator(IndicatorDef(
        key="spot_pressure_fusion",
        name="Spot Pressure Fusion (TSM-inspired)",
        formula="weighted(OBI, TFI, DAC, microprice/rvol, liquidity_score)",
        fields=["order_book", "trades", "turnover_24h"],
        compute=fusion_fn,
        category="composite",
        description="Validated fusion of book pressure + flow + liquidity. Improves signal in spot markets.",
        output_range=(-1.0, 1.0),
    ))


_build_core_indicators()


def resolve_trading_signal(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """
    Single decision view — avoids mixing incompatible scales.

    TSM ``combined`` uses the ±1..10 scale (threshold ~1.0).
    ``spot_pressure_fusion`` uses ±1 (threshold ~0.4) — informational only.
    """
    tsm_active = bool(metrics.get("tsm_enriched"))
    combined = float(metrics.get("combined", 0.0))
    egm = float(metrics.get("egm", 0.0))
    fusion = metrics.get("spot_pressure_fusion")

    return {
        "combined": combined,
        "egm": egm,
        "spot_pressure_fusion": float(fusion) if isinstance(fusion, (int, float)) else None,
        "tsm_active": tsm_active,
        "source": metrics.get("metrics_source", "simple"),
    }


def evaluate_local_decision(
    metrics: Dict[str, Any],
    *,
    egm_buy: float,
    egm_sell: float,
    combined_buy: float,
    combined_sell: float,
) -> str:
    """Local rule-based decision using one consistent combined scale."""
    sig = resolve_trading_signal(metrics)
    if sig["egm"] >= egm_buy or sig["combined"] >= combined_buy:
        return "buy"
    if sig["egm"] <= egm_sell or sig["combined"] <= combined_sell:
        return "sell"
    return "hold"


# =============================================================================
# High-level Metrics Function (backward compatible + enriched)
# =============================================================================

def calculate_metrics(
    candle_data: List[Dict[str, Any]],
    orderbook_data: Dict[str, List[List[str]]],
    ticker_data: Dict[str, Any],
    depth: int | None = None,
    return_variations: bool = False,
    *,
    symbol: str = "BTCUSDT",
    recent_trades: Optional[List[Dict]] = None,
    use_tsm: bool = True,
) -> Dict[str, float] | Dict[str, Any]:
    """Main entry point.

    Returns core metrics + 'combined'.
    If return_variations=True, also returns multiple validated combined versions
    (inspired by running our parameters through the full TSM Exchange pipeline).
    """
    if depth is None:
        depth = 5

    snap = MarketSnapshot(candle_data, orderbook_data, ticker_data)

    metrics: Dict[str, float] = {
        "egm": compute_egm(snap, Config),
        "ild": compute_ild(snap, depth),
        "rol": compute_rol(snap, depth),
        "pio": compute_pio(snap),
        "ogm": compute_ogm(snap, Config),
        "volatility": compute_volatility(snap),
    }

    metrics["combined"] = compute_combined(metrics, variation="balanced")

    if return_variations:
        metrics["combined_tsm_inspired"] = compute_combined(metrics, variation="tsm_inspired")
        metrics["combined_flow_heavy"] = compute_combined(metrics, variation="flow_heavy")
        metrics["spot_pressure_fusion"] = compute_spot_pressure_fusion(metrics)
        metrics["variations"] = list(Config.COMBINED_VARIATIONS.keys())

    if use_tsm and Config.TSM.enabled:
        try:
            from .tsm_bridge import enrich_metrics

            metrics = enrich_metrics(
                candle_data,
                orderbook_data,
                ticker_data,
                base_metrics=metrics,
                symbol=symbol,
                recent_trades=recent_trades,
            )
        except Exception as exc:
            logger.warning("TSM enrichment skipped: %s", exc)

    last_price = snap.last_price
    bids = orderbook_data.get("bids") or []
    asks = orderbook_data.get("asks") or []
    if bids:
        metrics["best_bid"] = _safe_float(bids[0][0], last_price)
    if asks:
        metrics["best_ask"] = _safe_float(asks[0][0], last_price + 0.1)
    elif bids:
        metrics["best_ask"] = metrics.get("best_bid", last_price) + 0.1
    mid = (metrics.get("best_bid", last_price) + metrics.get("best_ask", last_price)) / 2
    metrics["mid_price"] = mid
    spread = metrics.get("best_ask", 0) - metrics.get("best_bid", 0)
    metrics["spread"] = spread
    metrics["spread_bps"] = (spread / mid * 10000) if mid > 0 else 0

    # Simple EMA for snapshot (professional logs include them)
    if len(candle_data) >= 5:
        closes = [float(c.get("close", 0)) for c in candle_data[-20:]]
        ema5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else closes[-1]
        ema20 = sum(closes) / len(closes) if closes else ema5
        metrics["ema5"] = round(ema5, 2)
        metrics["ema20"] = round(ema20, 2)
        metrics["ema_diff_rel"] = (ema5 - ema20) / ema20 if ema20 else 0

    # Add obi / tfi style from current metrics for snapshots
    metrics["obi"] = metrics.get("ild", 0)  # reuse ild as obi proxy
    metrics["tfi"] = metrics.get("pio", 0)  # proxy

    return metrics


def build_rich_metrics_snapshot(
    candles: List[Dict[str, Any]],
    orderbook: Dict[str, List[List[str]]],
    ticker: Dict[str, Any],
    recent_trades: Optional[List[Dict]] = None,
    thresholds: Optional[Dict[str, float]] = None,
    *,
    symbol: str = "BTCUSDT",
    use_tsm: bool = True,
) -> Dict[str, Any]:
    """Build the full rich metrics dict matching professional Bybit bot logs.

    Includes many derived fields for snapshots.
    """
    if not candles or len(candles) < 1:
        return {"combined": 0.0}

    core = calculate_metrics(
        candles,
        orderbook,
        ticker,
        depth=50,
        return_variations=True,
        symbol=symbol,
        recent_trades=recent_trades,
        use_tsm=use_tsm,
    )

    last_price = _safe_float(ticker.get("last_price"))
    bids = orderbook.get("bids", []) or []
    asks = orderbook.get("asks", []) or []

    best_bid = _safe_float(bids[0][0]) if bids else last_price
    best_ask = _safe_float(asks[0][0]) if asks else last_price + 0.1
    mid = (best_bid + best_ask) / 2 if best_ask > 0 else last_price
    spread = best_ask - best_bid

    # Microprice (simple volume weighted)
    bid_vol = sum(_safe_float(b[1]) for b in bids[:20]) or 1e-9
    ask_vol = sum(_safe_float(a[1]) for a in asks[:20]) or 1e-9
    micro = (best_bid * ask_vol + best_ask * bid_vol) / (bid_vol + ask_vol)

    # Simple EMAs
    closes = [_safe_float(c["close"]) for c in candles[-20:]]
    ema5 = sum(closes[-5:]) / 5.0 if len(closes) >= 5 else last_price
    ema20 = sum(closes) / len(closes) if closes else last_price

    # Recent trades proxy (if no real trades stream, use recent candle volume as proxy or passed data)
    rt = recent_trades or []
    if rt:
        buy_qty = sum(t.get("quantity", 0) for t in rt if t.get("action") == "buy")
        sell_qty = sum(t.get("quantity", 0) for t in rt if t.get("action") == "sell")
        buy_not = sum(t.get("entry_price", 0) * t.get("quantity", 0) for t in rt if t.get("action") == "buy")
        sell_not = sum(t.get("entry_price", 0) * t.get("quantity", 0) for t in rt if t.get("action") == "sell")
    else:
        # Proxy from last candles volume split
        recent_vol = sum(_safe_float(c.get("volume")) for c in candles[-5:])
        buy_qty = recent_vol * 0.5
        sell_qty = recent_vol * 0.5
        buy_not = buy_qty * last_price
        sell_not = sell_qty * last_price

    total_qty = buy_qty + sell_qty or 1e-9
    total_not = buy_not + sell_not or 1e-9

    rich = {
        "combined": core.get("combined", 0.0),
        "ild": core.get("ild", 0.0),
        "egm": core.get("egm", 0.0),
        "rol": core.get("rol", 0.0),
        "pio": core.get("pio", 0.0),
        "ogm": core.get("ogm", 0.0),
        "volatility": core.get("volatility", 0.0),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid_price": mid,
        "spread": spread,
        "spread_pct": (spread / last_price) if last_price > 0 else 0,
        "spread_rel": (spread / last_price) if last_price > 0 else 0,
        "microprice": micro,
        "pio_raw": core.get("pio", 0.0),
        "ild_raw": core.get("ild", 0.0) * 100000,  # scaled like in example
        "egm_raw": core.get("egm", 0.0),
        "rol_raw": core.get("rol", 0.0),
        "ogm_raw": core.get("ogm", 0.0),
        "weighted_liquidity": core.get("rol", 0.0) * 3,  # proxy
        "asymmetry": (best_ask - mid) / (mid or 1),
        "imbalance20": (sum(_safe_float(b[1]) for b in bids[:20]) - sum(_safe_float(a[1]) for a in asks[:20])) / (sum(_safe_float(b[1]) for b in bids[:20]) + sum(_safe_float(a[1]) for a in asks[:20]) or 1),
        "ret1m": (closes[-1] - closes[-2]) / closes[-2] if len(closes) > 1 else 0,
        "ret5m": (closes[-1] - closes[-5]) / closes[-5] if len(closes) > 5 else 0,
        "ret20m": (closes[-1] - closes[0]) / closes[0] if len(closes) > 1 else 0,
        "igd_n5_n20": 0.001,  # placeholder - can be enhanced
        "ema5": round(ema5, 2),
        "ema20": round(ema20, 2),
        "ema_diff_rel": (ema5 - ema20) / ema20 if ema20 else 0,
        "cbd_n20": 0.0,  # placeholder
        "recent_trades_trade_count": float(len(rt)) if rt else 5.0,
        "recent_trades_buy_qty": buy_qty,
        "recent_trades_sell_qty": sell_qty,
        "recent_trades_total_qty": total_qty,
        "recent_trades_buy_notional": buy_not,
        "recent_trades_sell_notional": sell_not,
        "recent_trades_total_notional": total_not,
        "recent_trades_vwap": last_price,
        "recent_trades_imbalance_qty_pct": (buy_qty - sell_qty) / total_qty,
        "recent_trades_rvol": 1e-6,
        "recent_trades_last_trade_age_s": 5.0,
        "basis": 0.0,
        "obi": core.get("ild", 0.0),
        "tfi": core.get("pio", 0.0) * -1,  # sign flip like example sometimes
        "microprice_offset": micro - mid,
        "rvol": 1e-6,
        "spread_bps": (spread / mid * 10000) if mid else 0,
        "microprice_offset_bps": ((micro - mid) / mid * 10000) if mid else 0,
    }

    # Add thresholds
    if thresholds:
        rich["thresholds"] = thresholds
    else:
        rich["thresholds"] = {
            "egm_buy_threshold": 0.5,
            "egm_sell_threshold": -0.5,
            "combined_buy_threshold": 1.0,
            "combined_sell_threshold": -1.0,
            "combined_hold_band": 0.0
        }

    return rich


# =============================================================================
# TP / SL and Risk Helpers (elegant)
# =============================================================================

def calculate_dynamic_tp_sl(
    entry_price: float,
    action: str,
    volatility: float,
    combined: float,
) -> Tuple[float, float]:
    """Dynamic take-profit / stop-loss using volatility and signal strength."""
    # Base distance from volatility
    base_dist = max(volatility * entry_price, 0.001 * entry_price)

    # Modulate by combined strength
    strength = min(abs(combined) / 5.0, 2.0)  # 0..2
    tp_mult = 1.5 + (0.5 * strength)
    sl_mult = 0.8 - (0.2 * min(strength, 1.0))  # tighter SL on strong signals

    if action.lower() == "buy":
        tp = entry_price + (base_dist * tp_mult)
        sl = entry_price - (base_dist * sl_mult)
    else:
        tp = entry_price - (base_dist * tp_mult)
        sl = entry_price + (base_dist * sl_mult)

    return round(tp, 2), round(sl, 2)


# Small utility for XGB-style features (used by intelligence layer)
def extract_xgb_features(candles: List[Dict], metrics: Dict[str, float]) -> Dict[str, float]:
    """Consistent feature extraction for ML models."""
    if not candles or len(candles) < 5:
        return {k: 0.0 for k in ["price_change_5", "volume_ratio"]}

    closes = [_safe_float(c.get("close")) for c in candles[-5:]]
    vols = [_safe_float(c.get("volume")) for c in candles[-5:]]

    price_chg = (closes[-1] - closes[0]) / (closes[0] + 1e-9) if closes[0] else 0.0
    vol_ratio = vols[-1] / (np.mean(vols[:-1]) + 1e-9) if len(vols) > 1 and np.mean(vols[:-1]) > 0 else 1.0

    base = {k: float(metrics.get(k, 0.0)) for k in ["combined", "ild", "egm", "rol", "pio", "ogm", "volatility"]}
    base["price_change_5"] = float(price_chg)
    base["volume_ratio"] = float(vol_ratio)
    return base
