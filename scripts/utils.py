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
    **kwargs: Any,
) -> Dict[str, float]:
    """Delegate to scripts.formulas — single metrics implementation."""
    try:
        from scripts.formulas import calculate_metrics as _calc
    except ImportError:
        from formulas import calculate_metrics as _calc

    symbol = kwargs.pop("symbol", os.getenv("SYMBOL", "BTCUSDT"))
    return _calc(
        candle_data,
        orderbook_data,
        ticker_data,
        depth=depth,
        symbol=symbol,
        use_tsm=kwargs.pop("use_tsm", True),
        return_variations=kwargs.pop("return_variations", False),
        recent_trades=kwargs.pop("recent_trades", None),
    )


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