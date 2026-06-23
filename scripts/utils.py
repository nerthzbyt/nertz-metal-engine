import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Protocol

import numpy as np

logger = logging.getLogger("NertzUtils")

# Define a protocol for the configuration to help type checking
class ConfigProtocol(Protocol):
    METRIC_WEIGHTS: Dict[str, float]
    COMBINED_SCALE: float
    COMBINED_CLAMP: float
    MIN_PRICE_RANGE_PCT: float
    SPREAD_REFERENCE: float
    EPSILON: float
    ORDERBOOK_DEPTH: int

# ── Referencia lazy a la configuración (se inyecta al arrancar) ──
_config: Optional[ConfigProtocol] = None


def set_config(cfg: ConfigProtocol) -> None:
    """Inyecta la instancia de ConfigSettings para eliminar hardcodeos."""
    global _config
    _config = cfg


def _cfg():
    """Acceso seguro a la configuración; si no está disponible, usa defaults."""
    if _config is None:
        # Fallback ultra-mínimo para evitar crashes
        class _Fallback:
            METRIC_WEIGHTS = {"egm": 0.20, "ild": 0.30, "rol": 0.30, "pio": 0.10, "ogm": 0.10}
            COMBINED_SCALE = 10.0
            COMBINED_CLAMP = 10.0
            MIN_PRICE_RANGE_PCT = 0.01
            SPREAD_REFERENCE = 0.02
            EPSILON = 1e-8
            ORDERBOOK_DEPTH = 5
        return _Fallback()
    return _config


# ═══════════════════════════════════════════════════════════
#  Cálculo de métricas (sin hardcodeos)
# ═══════════════════════════════════════════════════════════

def calculate_metrics(
    candle_data: List[Dict[str, float]],
    orderbook_data: Dict[str, List[List[str]]],
    ticker_data: Dict[str, float],
    depth: Optional[int] = None,
) -> Dict[str, float]:
    """
    Calcula métricas de señal usando parámetros configurables.
    Sin hardcodeos — todo viene de ConfigSettings.
    """
    cfg = _cfg()

    if depth is None:
        depth = getattr(cfg, "ORDERBOOK_DEPTH", 5)

    # Elegant guard with diagnostics
    reasons = []
    if not candle_data or len(candle_data) < 2:
        reasons.append("insufficient candles")
    if not orderbook_data.get("bids") or not orderbook_data.get("asks"):
        reasons.append("empty orderbook")
    if not ticker_data or not ticker_data.get("last_price"):
        reasons.append("missing ticker last_price")

    if reasons:
        logger.warning(f"Datos insuficientes para métricas ({', '.join(reasons)}), usando defaults")
        return _default_metrics()

    try:
        last_price: float = float(ticker_data["last_price"])
        eps = cfg.EPSILON

        # ── Velas ──
        closes = np.array([float(c["close"]) for c in candle_data[-5:]], dtype=np.float64)
        highs = np.array([float(c.get("high", 0)) for c in candle_data[-5:] if c.get("high") is not None], dtype=np.float64)
        lows = np.array([float(c.get("low", 0)) for c in candle_data[-5:] if c.get("low") is not None], dtype=np.float64)

        if len(highs) == 0 or len(lows) == 0:
            logger.warning("Datos insuficientes para calcular altos y bajos")
            return _default_metrics()

        # ── Volatilidad ──
        avg_price = float(np.mean(closes))
        price_range = float(max(highs.max() - lows.min(), getattr(cfg, "MIN_PRICE_RANGE_PCT", 0.01) * last_price))
        volatility = (highs.max() - lows.min()) / (last_price + eps)

        # ── Orderbook ──
        bids = orderbook_data["bids"][:depth]
        asks = orderbook_data["asks"][:depth]

        bid_volume = sum(float(bid[1]) for bid in bids if float(bid[1]) > 0)
        ask_volume = sum(float(ask[1]) for ask in asks if float(ask[1]) > 0)
        total_volume = bid_volume + ask_volume + eps
        ild = np.clip((bid_volume - ask_volume) / total_volume, -1.0, 1.0)

        # ── EGM ──
        egm = np.clip((last_price - avg_price) / (price_range + eps), -1.0, 1.0)

        # ── ROL ──
        bid_value = sum(float(bid[0]) * float(bid[1]) for bid in bids if float(bid[1]) > 0)
        ask_value = sum(float(ask[0]) * float(ask[1]) for ask in asks if float(ask[1]) > 0)
        total_value = bid_value + ask_value + eps
        rol = np.clip((bid_value - ask_value) / total_value, -1.0, 1.0)

        # ── PIO (Presión de volumen) ──
        volumes = np.array([max(float(c.get("volume", 0)), eps) for c in candle_data[-5:]], dtype=np.float64)
        pio = 0.0
        if len(volumes) >= 2:
            avg_volume = float(np.mean(volumes[:-1]))
            pio = np.clip((volumes[-1] - avg_volume) / (avg_volume + eps), -1.0, 1.0)

        # ── OGM ──
        best_bid = float(bids[0][0]) if bids else last_price
        best_ask = float(asks[0][0]) if asks else last_price
        spread = (best_ask - best_bid) / (last_price + eps)
        ogm = 1.0 - np.clip(spread / getattr(cfg, "SPREAD_REFERENCE", 0.02), 0.0, 1.0)

        # ── Combined (pesos configurables) ──
        w = getattr(cfg, "METRIC_WEIGHTS", {"egm": 0.20, "ild": 0.30, "rol": 0.30, "pio": 0.10, "ogm": 0.10})
        combined = np.clip(
            (w.get("egm", 0.2) * egm + w.get("ild", 0.3) * ild + w.get("rol", 0.3) * rol +
             w.get("pio", 0.1) * pio + w.get("ogm", 0.1) * ogm) * getattr(cfg, "COMBINED_SCALE", 10.0),
            -getattr(cfg, "COMBINED_CLAMP", 10.0), getattr(cfg, "COMBINED_CLAMP", 10.0),
        )

        logger.debug(
            f"Métricas: combined={combined:.2f}, ild={ild:.4f}, egm={egm:.4f}, "
            f"rol={rol:.4f}, pio={pio:.4f}, ogm={ogm:.4f}, volatility={volatility:.4f}"
        )

        return {
            "combined": float(combined),
            "ild": float(ild),
            "egm": float(egm),
            "rol": float(rol),
            "pio": float(pio),
            "ogm": float(ogm),
            "volatility": float(volatility),
        }

    except Exception as e:
        logger.error(f"Error en calculate_metrics: {e}", exc_info=True)
        return _default_metrics()


def _default_metrics() -> Dict[str, float]:
    return {"combined": 0.0, "ild": 0.0, "egm": 0.0, "rol": 0.0,
            "pio": 0.0, "ogm": 0.0, "volatility": 0.0}


# ═══════════════════════════════════════════════════════════
#  Persistencia de resultados
# ═══════════════════════════════════════════════════════════

def save_results(results: dict, log_dir: str) -> None:
    os.makedirs(log_dir, exist_ok=True)
    file_path = os.path.join(log_dir, "results.json")
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=4)
    logger.info(f"Resultados guardados en: {file_path}")


# ═══════════════════════════════════════════════════════════
#  Utilidades de tiempo
# ═══════════════════════════════════════════════════════════

def timestamp_to_datetime(timestamp: int) -> datetime:
    return datetime.utcfromtimestamp(timestamp // 1000).replace(tzinfo=timezone.utc)


# ═══════════════════════════════════════════════════════════
#  Cálculo dinámico de TP / SL
# ═══════════════════════════════════════════════════════════

def calculate_tp_sl(
    price: float, volatility: float, action: str,
    tp_factor: float = 1.5, sl_factor: float = 1.0,
) -> Tuple[float, float]:
    """Calcula Take Profit y Stop Loss dinámicos."""
    price_range = volatility * price
    if action.lower() == "buy":
        tp = price + (price_range * tp_factor)
        sl = price - (price_range * sl_factor)
    else:  # sell
        tp = price - (price_range * tp_factor)
        sl = price + (price_range * sl_factor)
    return round(tp, 2), round(sl, 2)


# ═══════════════════════════════════════════════════════════
#  Clase de estrategia TP/SL
# ═══════════════════════════════════════════════════════════

# BaseTradingStrategy remnant from advanced indicator module (unused in core bot).
# It was a placeholder for connector/data_manager (high-freq trading infrastructure).
# Removed to avoid dead code and simplify. Core bot uses direct metrics + combined signal.
class BaseTradingStrategy:
    def __init__(self, **kwargs):
        self.logger = logging.getLogger("NertzMetalEngine")


def evaluate_trend(short_ema: float, mid_ema: float, long_ema: float) -> Tuple[str, str]:
    if short_ema > mid_ema > long_ema:
        return "BUY", "Cruzamiento alcista de Triple EMA"
    elif short_ema < mid_ema < long_ema:
        return "SELL", "Cruzamiento bajista de Triple EMA"
    return "HOLD", "No hay confirmación clara"


class TpslStrategy(BaseTradingStrategy):
    def __init__(
        self,
        short_window: int = 5, mid_window: int = 10, long_window: int = 20,
        tp_percentage: float = 1.5, sl_percentage: float = 0.5,
        combined_buy_threshold: float = 2.0, combined_sell_threshold: float = -3.0,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.short_window = short_window
        self.mid_window = mid_window
        self.long_window = long_window
        self.tp_percentage = tp_percentage
        self.sl_percentage = sl_percentage
        self.combined_buy_threshold = combined_buy_threshold
        self.combined_sell_threshold = combined_sell_threshold

    def calculate_ema(self, prices: List[float], window: int) -> float:
        if len(prices) < window:
            return sum(prices[-window:]) / min(len(prices), window)
        alpha = 2 / (window + 1)
        ema = prices[-window]
        for price in prices[-window + 1:]:
            ema = (price * alpha) + (ema * (1 - alpha))
        return ema

    def generate_signal(
        self, market_data: Dict[str, List[float]], metrics: Dict[str, float]
    ) -> Dict[str, Any]:
        if "close_prices" not in market_data or not market_data["close_prices"]:
            self.logger.warning("⚠️ Faltan datos de 'close_prices' en market_data.")
            return _empty_signal("Sin datos")

        closing_prices = market_data["close_prices"]
        if len(closing_prices) < self.long_window:
            self.logger.warning(f"⚠️ No hay suficientes datos (mínimo {self.long_window} precios).")
            return _empty_signal("Datos insuficientes")

        short_ema = self.calculate_ema(closing_prices, self.short_window)
        mid_ema = self.calculate_ema(closing_prices, self.mid_window)
        long_ema = self.calculate_ema(closing_prices, self.long_window)

        latest_price = closing_prices[-1]
        action, reason = evaluate_trend(short_ema, mid_ema, long_ema)

        if action == "SELL" and metrics.get("combined", 0.0) > self.combined_sell_threshold:
            action, reason = "HOLD", "Venta suspendida por combined insuficiente"
        if action == "BUY" and metrics.get("combined", 0.0) < self.combined_buy_threshold:
            action, reason = "HOLD", "Compra invalidada por combined insuficiente"

        # Pass advanced metrics for fast TP/SL calibration (combined + pressure for high-freq)
        vol = metrics.get("volatility", 0.0)
        comb = metrics.get("combined", 0.0)
        press = metrics.get("orderbook_pressure", metrics.get("ild", 0.0))
        take_profit, stop_loss = self.calculate_take_profit_stop_loss(
            latest_price, action, vol, comb, press
        )
        return {
            "action": action,
            "confidence": 0.9 if action in ["BUY", "SELL"] else 0.5,
            "take_profit": take_profit,
            "stop_loss": stop_loss,
            "reason": reason,
            "metrics": metrics,
        }

    def calculate_take_profit_stop_loss(
        self, latest_price: float, action: str, volatility: float, combined: float = 0.0, pressure: float = 0.0
    ) -> Tuple[float, float]:
        # Professional fast TP/SL: tighter when combined or pressure is strong (high-freq bias)
        # No hardcodes — scales with volatility + signal strength from advanced indicators
        factor = 1.0
        if combined > 2.0 or pressure > 0.3:
            factor = 0.6  # faster close on strong signal
        elif combined < -2.0 or pressure < -0.3:
            factor = 0.8

        if action.upper() == "BUY":
            tp = latest_price * (1 + (self.tp_percentage * factor + (volatility * 8)) / 100)
            sl = latest_price * (1 - (self.sl_percentage * factor + (volatility * 4)) / 100)
        elif action.upper() == "SELL":
            tp = latest_price * (1 - (self.tp_percentage * factor + (volatility * 8)) / 100)
            sl = latest_price * (1 + (self.sl_percentage * factor + (volatility * 4)) / 100)
        else:
            tp = sl = 0.0
        return round(tp, 4), round(sl, 4)  # higher precision for high-freq


def _empty_signal(reason: str) -> Dict[str, Any]:
    return {"action": "HOLD", "confidence": 0.0, "take_profit": 0.0,
            "stop_loss": 0.0, "reason": reason}


# =============================================================================
# Rich results handling (inspired by sibling project for professional logging)
# Use append for event based, to keep history of snapshots without bloat.
# =============================================================================

_RESULTS_LOCK = threading.Lock() if 'threading' in globals() else None
import threading as _threading
_RESULTS_LOCK = _threading.Lock()

def append_results_event(event: Dict[str, Any], log_dir: str = "logs") -> None:
    """Append a rich event (metrics, balance, trade, etc) to results.json.
    Keeps the structure with metadata + list of events/snapshots like professional logs.
    """
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "results.json")
    with _RESULTS_LOCK:
        data = load_results_json(log_dir)
        if "events" not in data:
            data["events"] = []
        data["events"].append(event)
        # Update metadata timestamp
        if "metadata" not in data:
            data["metadata"] = {}
        data["metadata"]["timestamp"] = datetime.now(timezone.utc).isoformat()
        save_results(data, log_dir)

def load_results_json(log_dir: str = "logs") -> Dict[str, Any]:
    path = os.path.join(log_dir, "results.json")
    if not os.path.exists(path):
        return {"metadata": {}, "events": [], "trades": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"metadata": {}, "events": [], "trades": {}}

def save_results_async(results: dict, log_dir: str = "logs") -> None:
    # For compatibility, delegate to sync for now (can make async later)
    save_results(results, log_dir)

def append_results_event_async(event: Dict[str, Any], log_dir: str = "logs") -> None:
    append_results_event(event, log_dir)

def load_results_json_async(log_dir: str = "logs") -> Dict[str, Any]:
    return load_results_json(log_dir)


def pnl_stats(trades: List[Dict[str, Any]]) -> Dict[str, float]:
    """Shared PnL stats helper (used in profit endpoints)."""
    prof = sum(t["profit_loss"] for t in trades if t.get("profit_loss", 0) > 0)
    loss = sum(t["profit_loss"] for t in trades if t.get("profit_loss", 0) < 0)
    return {"profit": round(prof, 2), "loss": round(loss, 2), "net": round(prof + loss, 2)}