import json
import logging
import os
from datetime import datetime, timezone
from typing import Tuple, Dict, List

import numpy as np

logger = logging.getLogger("NertzUtils")


def calculate_metrics(candle_data: List[Dict[str, float]], orderbook_data: Dict[str, List[List[str]]],
                      ticker_data: Dict[str, float], depth: int = 5) -> Dict[str, float]:
    if not all([candle_data, len(candle_data) >= 2, orderbook_data.get("bids"), orderbook_data.get("asks"),
                ticker_data.get('last_price')]):
        logger.warning("Datos insuficientes para métricas, devolviendo valores por defecto")
        return {"combined": 0.0, "ild": 0.0, "egm": 0.0, "rol": 0.0, "pio": 0.0, "ogm": 0.0, "volatility": 0.0}

    try:
        last_price: float = float(ticker_data["last_price"])

        # Ajuste para volumen bajo
        volumes = np.array([max(float(c.get("volume", 0)), 0.0001) for c in candle_data[-5:]], dtype=np.float64)
        closes = np.array([float(c["close"]) for c in candle_data[-5:]], dtype=np.float64)
        highs = np.array([float(c.get("high", 0)) for c in candle_data[-5:] if c.get("high") is not None],
                         dtype=np.float64)
        lows = np.array([float(c.get("low", 0)) for c in candle_data[-5:] if c.get("low") is not None],
                        dtype=np.float64)

        if len(highs) == 0 or len(lows) == 0:
            logger.warning("Datos insuficientes para calcular altos y bajos, devolviendo valores por defecto")
            return {"combined": 0.0, "ild": 0.0, "egm": 0.0, "rol": 0.0, "pio": 0.0, "ogm": 0.0, "volatility": 0.0}

        avg_price = float(np.mean(closes))
        price_range = float(max(highs.max() - lows.min(), 0.01 * last_price))  # Mínimo 1% del precio
        volatility = (highs.max() - lows.min()) / last_price

        bids = orderbook_data["bids"][:depth]
        asks = orderbook_data["asks"][:depth]
        bid_volume = sum(float(bid[1]) for bid in bids if float(bid[1]) > 0)
        ask_volume = sum(float(ask[1]) for ask in asks if float(ask[1]) > 0)
        total_volume = bid_volume + ask_volume + 1e-6
        ild = np.clip((bid_volume - ask_volume) / total_volume, -1.0, 1.0)

        egm = np.clip((last_price - avg_price) / price_range, -1.0, 1.0)

        bid_value = sum(float(bid[0]) * float(bid[1]) for bid in bids if float(bid[1]) > 0)
        ask_value = sum(float(ask[0]) * float(ask[1]) for ask in asks if float(ask[1]) > 0)
        total_value = bid_value + ask_value + 1e-6
        rol = np.clip((bid_value - ask_value) / total_value, -1.0, 1.0)

        pio = 0.0
        if len(volumes) >= 2:
            avg_volume = float(np.mean(volumes[1:]))
            pio = np.clip((volumes[0] - avg_volume) / (avg_volume + 1e-6), -1.0, 1.0)

        best_bid = float(bids[0][0]) if bids else last_price
        best_ask = float(asks[0][0]) if asks else last_price
        spread = (best_ask - best_bid) / last_price
        ogm = 1.0 - np.clip(spread / 0.02, 0, 1.0)

        combined = np.clip((0.2 * egm + 0.3 * ild + 0.3 * rol + 0.1 * pio + 0.1 * ogm) * 10, -10.0, 10.0)

        logger.debug(
            f"Métricas: combined={combined:.2f}, ild={ild:.4f}, egm={egm:.4f}, rol={rol:.4f}, pio={pio:.4f}, ogm={ogm:.4f}, volatility={volatility:.4f}")
        return {
            "combined": float(combined),
            "ild": float(ild),
            "egm": float(egm),
            "rol": float(rol),
            "pio": float(pio),
            "ogm": float(ogm),
            "volatility": float(volatility)
        }
    except Exception as e:
        logger.error(f"Error en calculate_metrics: {e}", exc_info=True)
        return {"combined": 0.0, "ild": 0.0, "egm": 0.0, "rol": 0.0, "pio": 0.0, "ogm": 0.0, "volatility": 0.0}


def save_results(results: dict, log_dir: str, session_start: str = None):
    """Guarda los resultados generados por el motor en un archivo JSON."""
    os.makedirs(log_dir, exist_ok=True)
    timestamp_str = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    file_path = os.path.join(log_dir, f"results_{timestamp_str}.json")
    # Also save a latest snapshot for the API
    latest_path = os.path.join(log_dir, "results.json")
    with open(file_path, "w") as file:
        json.dump(results, file, indent=4)
    with open(latest_path, "w") as file:
        json.dump(results, file, indent=4)
    logger.info(f"Resultados guardados en: {file_path}")



def timestamp_to_datetime (timestamp: int) -> datetime:
    """Convierte un timestamp a un objeto datetime."""
    return datetime.utcfromtimestamp (timestamp // 1000).replace (tzinfo=timezone.utc)



def calculate_tp_sl(price: float, volatility: float, action: str, tp_factor: float = 1.5, sl_factor: float = 1.0) -> \
Tuple[float, float]:
    """Calcula Take Profit y Stop Loss dinámicos."""
    try:
        price_range = volatility * price
        if action.lower() == "buy":
            tp = price + (price_range * tp_factor)
            sl = price - (price_range * sl_factor)
        else:  # sell
            tp = price - (price_range * tp_factor)
            sl = price + (price_range * sl_factor)
        return round(tp, 2), round(sl, 2)
    except Exception as e:
        logger.error(f"❌ Error en calculate_tp_sl: {e}")
        return 0.0, 0.0


# Clases de estrategia (sin cambios, pero validadas)
class BaseTradingStrategy:
    def __init__(self, connector=None, data_manager=None, **kwargs):
        self.logger = logging.getLogger("NertzMetalEngine")
        self.connector = connector
        self.data_manager = data_manager


def evaluate_trend(short_ema: float, mid_ema: float, long_ema: float) -> Tuple[str, str]:
    """Evalúa la tendencia para decidir la acción de trading."""
    if short_ema > mid_ema > long_ema:
        return "BUY", "Cruzamiento alcista de Triple EMA"
    elif short_ema < mid_ema < long_ema:
        return "SELL", "Cruzamiento bajista de Triple EMA"
    return "HOLD", "No hay confirmación clara"


class TpslStrategy(BaseTradingStrategy):
    def __init__(self, connector=None, data_manager=None, short_window: int = 5, mid_window: int = 10,
                 long_window: int = 20,
                 tp_percentage: float = 1.5, sl_percentage: float = 0.5, combined_buy_threshold: float = 2.0,
                 combined_sell_threshold: float = -3.0, **kwargs):
        super().__init__(connector, data_manager, **kwargs)
        self.short_window = short_window
        self.mid_window = mid_window
        self.long_window = long_window
        self.tp_percentage = tp_percentage
        self.sl_percentage = sl_percentage
        self.combined_buy_threshold = combined_buy_threshold
        self.combined_sell_threshold = combined_sell_threshold

    def calculate_ema(self, prices: List[float], window: int) -> float:
        """Calcula EMA de manera pura en Python."""
        if len(prices) < window:
            return sum(prices[-window:]) / min(len(prices), window)
        alpha = 2 / (window + 1)
        ema = prices[-window]
        for price in prices[-window + 1:]:
            ema = (price * alpha) + (ema * (1 - alpha))
        return ema

    def generate_signal(self, market_data: Dict[str, List[float]], metrics: Dict[str, float]) -> Dict[str, any]:
        """Genera señales basadas en Triple EMA, TP/SL y métricas."""
        if "close_prices" not in market_data or not market_data["close_prices"]:
            self.logger.warning("⚠️ Faltan datos de 'close_prices' en market_data.")
            return {"action": "HOLD", "confidence": 0.0, "take_profit": 0.0, "stop_loss": 0.0, "reason": "Sin datos"}

        closing_prices = market_data["close_prices"]

        if len(closing_prices) < self.long_window:
            self.logger.warning(f"⚠️ No hay suficientes datos (mínimo {self.long_window} precios).")
            return {"action": "HOLD", "confidence": 0.0, "take_profit": 0.0, "stop_loss": 0.0,
                    "reason": "Datos insuficientes"}

        short_ema = self.calculate_ema(closing_prices, self.short_window)
        mid_ema = self.calculate_ema(closing_prices, self.mid_window)
        long_ema = self.calculate_ema(closing_prices, self.long_window)

        latest_price = closing_prices[-1]
        action, reason = evaluate_trend(short_ema, mid_ema, long_ema)

        if action == "SELL" and metrics.get("combined", 0.0) > self.combined_sell_threshold:
            action = "HOLD"
            reason = "Venta suspendida por combined insuficiente"

        if action == "BUY" and metrics.get("combined", 0.0) < self.combined_buy_threshold:
            action = "HOLD"
            reason = "Compra invalidada por combined insuficiente"

        take_profit, stop_loss = self.calculate_take_profit_stop_loss(latest_price, action,
                                                                      metrics.get("volatility", 0.0))

        return {
            "action": action,
            "confidence": 0.9 if action in ["BUY", "SELL"] else 0.5,
            "take_profit": take_profit,
            "stop_loss": stop_loss,
            "reason": reason,
            "metrics": metrics
        }

    def calculate_take_profit_stop_loss(self, latest_price: float, action: str, volatility: float) -> Tuple[
        float, float]:
        """Calcula TP y SL con ajuste dinámico por volatilidad."""
        if action == "BUY":
            take_profit = latest_price * (1 + (self.tp_percentage + (volatility * 10)) / 100)
            stop_loss = latest_price * (1 - (self.sl_percentage + (volatility * 5)) / 100)
        elif action == "SELL":
            take_profit = latest_price * (1 - (self.tp_percentage + (volatility * 10)) / 100)
            stop_loss = latest_price * (1 + (self.sl_percentage + (volatility * 5)) / 100)
        else:
            take_profit = stop_loss = 0.0
        return round(take_profit, 2), round(stop_loss, 2)