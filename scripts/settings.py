import os
import logging
import json
from typing import Any, Dict, Union

logger = logging.getLogger("NertzMetalEngine")


class ConfigSettings:
    """Configuración centralizada del motor de trading — sin hardcodeos."""

    DEFAULTS: Dict[str, Any] = {
        # ── Validación ──
        "VALID_SYMBOLS": ["BTCUSDT", "ETHUSDT", "XRPUSDT"],
        "VALID_TIMEFRAMES": ["1m", "5m", "15m", "1h", "4h", "1d"],
        "VALID_ORDER_TYPES": ["limit", "market"],
        "VALID_TIME_IN_FORCE": ["GoodTillCancel", "ImmediateOrCancel", "FillOrKill"],
        "VALID_ORDERBOOK_DEPTHS": [1, 5, 10, 25],
        "VALID_BYBIT_ENV": ["demo", "live"],

        # ── API / Entorno ──
        "DEFAULT_ORDERBOOK_DEPTH": 5,
        "DEFAULT_BYBIT_ENV": "demo",
        "DEFAULT_SLEEP_TIME": 60,

        # ── Capital y Riesgo ──
        "CAPITAL_USDT": 5000.0,
        "FEE_RATE": 0.002,
        "RISK_FACTOR": 0.01,
        "TP_PERCENTAGE": 0.02,
        "SL_PERCENTAGE": 0.01,
        "MIN_TRADE_SIZE": 0.001,
        "MAX_TRADE_SIZE": 100.0,
        "MAX_ITERATIONS": 1000,

        # ── Umbrales de estrategia ──
        "EGM_BUY_THRESHOLD": 0.5,
        "EGM_SELL_THRESHOLD": -0.5,
        "PIO_THRESHOLD": 0.1,

        # ── Cálculo de cantidad ──
        "BASE_VOLATILITY": 0.01,         # volatilidad de referencia para normalizar cantidad
        "MIN_VOLATILITY_FLOOR": 0.001,   # piso mínimo de volatilidad
        "MAX_POSITION_PCT": 0.10,        # % máximo del balance por posición

        # ── Métricas (utils.py) ──
        "METRIC_WEIGHTS": {              # pesos para combined (deben sumar 1.0)
            "egm": 0.20,
            "ild": 0.30,
            "rol": 0.30,
            "pio": 0.10,
            "ogm": 0.10,
        },
        "COMBINED_SCALE": 10.0,          # factor de escala de combined
        "COMBINED_CLAMP": 10.0,          # límite de clip (±)
        "MIN_PRICE_RANGE_PCT": 0.01,     # rango mínimo de precio (1 %)
        "SPREAD_REFERENCE": 0.02,        # spread de referencia para OGM
        "EPSILON": 1e-8,                 # valor infinitesimal para evitar división por cero

        # ── Throttling / Cooldown ──
        "KLINE_STALE_MS": 60_000,        # ms sin kline para activar fallback por ticker
        "TICKER_TRADE_COOLDOWN_S":5,   # segundos mínimo entre trades disparados por ticker
        "ORDERBOOK_LOG_INTERVAL_S": 5,   # segundos entre logs de orderbook
        "BALANCE_SYNC_INTERVAL_S": 15,   # segundos entre sincronizaciones de balance
        "BUFFER_SIZE": 10,               # tamaño del buffer de trades

        # ── Reintentos de órdenes ──
        "ORDER_MAX_RETRIES": 3,
        "ORDER_MAX_BACKOFF_S": 8,
        "ORDER_RETRY_BASE_DELAY_S": 2,

        # ── WebSocket / Red ──
        "WS_RECONNECT_DELAY_S": 5,
        "WS_MAX_RECONNECT_ATTEMPTS": 5,
        "HTTP_TIMEOUT_S": 10,
    }

    def __init__(self):
        # ── API Keys ──
        self.BYBIT_API_KEY = self._get_env("BYBIT_API_KEY")
        self.BYBIT_API_SECRET = self._get_env("BYBIT_API_SECRET")
        self.BYBIT_ENV = self._get_validated(
            "BYBIT_ENV", self.DEFAULTS["DEFAULT_BYBIT_ENV"], self.DEFAULTS["VALID_BYBIT_ENV"]
        )

        # ── Trading ──
        self.SYMBOL = self._get_validated("SYMBOL", "BTCUSDT", self.DEFAULTS["VALID_SYMBOLS"])
        self.TIMEFRAME = self._get_validated("TIMEFRAME", "1m", self.DEFAULTS["VALID_TIMEFRAMES"])
        self.ORDER_TYPE = self._get_validated("ORDER_TYPE", "limit", self.DEFAULTS["VALID_ORDER_TYPES"])
        self.TIME_IN_FORCE = self._get_validated("TIME_IN_FORCE", "GoodTillCancel", self.DEFAULTS["VALID_TIME_IN_FORCE"])
        self.ORDERBOOK_DEPTH = self._get_validated(
            "ORDERBOOK_DEPTH", self.DEFAULTS["DEFAULT_ORDERBOOK_DEPTH"],
            self.DEFAULTS["VALID_ORDERBOOK_DEPTHS"], cast_to=int
        )

        # ── Capital / Riesgo ──
        self.CAPITAL_USDT = self._get_float("CAPITAL_USDT", self.DEFAULTS["CAPITAL_USDT"], positive=True)
        self.FEE_RATE = self._get_float("FEE_RATE", self.DEFAULTS["FEE_RATE"], min_value=0.0, max_value=0.1)
        self.RISK_FACTOR = self._get_float("RISK_FACTOR", self.DEFAULTS["RISK_FACTOR"], min_value=0.0, max_value=1.0)
        self.TP_PERCENTAGE = self._get_float("TP_PERCENTAGE", self.DEFAULTS["TP_PERCENTAGE"])
        self.SL_PERCENTAGE = self._get_float("SL_PERCENTAGE", self.DEFAULTS["SL_PERCENTAGE"])
        self.MIN_TRADE_SIZE = self._get_float("MIN_TRADE_SIZE", self.DEFAULTS["MIN_TRADE_SIZE"])
        self.MAX_TRADE_SIZE = self._get_float("MAX_TRADE_SIZE", self.DEFAULTS["MAX_TRADE_SIZE"])
        self.MAX_ITERATIONS = int(self._get_float("MAX_ITERATIONS", self.DEFAULTS["MAX_ITERATIONS"]))
        self.DEFAULT_SLEEP_TIME = self._get_float("DEFAULT_SLEEP_TIME", self.DEFAULTS["DEFAULT_SLEEP_TIME"], positive=True)

        # ── Strategy Thresholds ──
        self.EGM_BUY_THRESHOLD = self._get_float("EGM_BUY_THRESHOLD", self.DEFAULTS["EGM_BUY_THRESHOLD"])
        self.EGM_SELL_THRESHOLD = self._get_float("EGM_SELL_THRESHOLD", self.DEFAULTS["EGM_SELL_THRESHOLD"])
        self.PIO_THRESHOLD = self._get_float("PIO_THRESHOLD", self.DEFAULTS["PIO_THRESHOLD"])

        # ── Nuevos parámetros: cálculo de cantidad ──
        self.BASE_VOLATILITY = self._get_float("BASE_VOLATILITY", self.DEFAULTS["BASE_VOLATILITY"], positive=True)
        self.MIN_VOLATILITY_FLOOR = self._get_float("MIN_VOLATILITY_FLOOR", self.DEFAULTS["MIN_VOLATILITY_FLOOR"], positive=True)
        self.MAX_POSITION_PCT = self._get_float("MAX_POSITION_PCT", self.DEFAULTS["MAX_POSITION_PCT"], min_value=0.0, max_value=1.0)

        # ── Nuevos parámetros: métricas ──
        weights_raw = self._get_env("METRIC_WEIGHTS")
        if weights_raw:
            try:
                self.METRIC_WEIGHTS = json.loads(weights_raw)
            except (json.JSONDecodeError, TypeError):
                logger.warning("⚠ METRIC_WEIGHTS inválido en .env, usando defaults")
                self.METRIC_WEIGHTS = dict(self.DEFAULTS["METRIC_WEIGHTS"])
        else:
            self.METRIC_WEIGHTS = dict(self.DEFAULTS["METRIC_WEIGHTS"])

        self.COMBINED_SCALE = self._get_float("COMBINED_SCALE", self.DEFAULTS["COMBINED_SCALE"])
        self.COMBINED_CLAMP = self._get_float("COMBINED_CLAMP", self.DEFAULTS["COMBINED_CLAMP"], positive=True)
        self.MIN_PRICE_RANGE_PCT = self._get_float("MIN_PRICE_RANGE_PCT", self.DEFAULTS["MIN_PRICE_RANGE_PCT"], positive=True)
        self.SPREAD_REFERENCE = self._get_float("SPREAD_REFERENCE", self.DEFAULTS["SPREAD_REFERENCE"], positive=True)
        self.EPSILON = self._get_float("EPSILON", self.DEFAULTS["EPSILON"], positive=True)

        # ── Nuevos parámetros: throttling ──
        self.KLINE_STALE_MS = int(self._get_float("KLINE_STALE_MS", self.DEFAULTS["KLINE_STALE_MS"], positive=True))
        self.TICKER_TRADE_COOLDOWN_S = self._get_float("TICKER_TRADE_COOLDOWN_S", self.DEFAULTS["TICKER_TRADE_COOLDOWN_S"], positive=True)
        self.ORDERBOOK_LOG_INTERVAL_S = self._get_float("ORDERBOOK_LOG_INTERVAL_S", self.DEFAULTS["ORDERBOOK_LOG_INTERVAL_S"], positive=True)
        self.BALANCE_SYNC_INTERVAL_S = self._get_float("BALANCE_SYNC_INTERVAL_S", self.DEFAULTS["BALANCE_SYNC_INTERVAL_S"], positive=True)
        self.BUFFER_SIZE = int(self._get_float("BUFFER_SIZE", self.DEFAULTS["BUFFER_SIZE"], positive=True))

        # ── Nuevos parámetros: reintentos ──
        self.ORDER_MAX_RETRIES = int(self._get_float("ORDER_MAX_RETRIES", self.DEFAULTS["ORDER_MAX_RETRIES"], positive=True))
        self.ORDER_MAX_BACKOFF_S = self._get_float("ORDER_MAX_BACKOFF_S", self.DEFAULTS["ORDER_MAX_BACKOFF_S"], positive=True)
        self.ORDER_RETRY_BASE_DELAY_S = self._get_float("ORDER_RETRY_BASE_DELAY_S", self.DEFAULTS["ORDER_RETRY_BASE_DELAY_S"], positive=True)

        # ── Nuevos parámetros: red ──
        self.WS_RECONNECT_DELAY_S = self._get_float("WS_RECONNECT_DELAY_S", self.DEFAULTS["WS_RECONNECT_DELAY_S"], positive=True)
        self.WS_MAX_RECONNECT_ATTEMPTS = int(self._get_float("WS_MAX_RECONNECT_ATTEMPTS", self.DEFAULTS["WS_MAX_RECONNECT_ATTEMPTS"], positive=True))
        self.HTTP_TIMEOUT_S = self._get_float("HTTP_TIMEOUT_S", self.DEFAULTS["HTTP_TIMEOUT_S"], positive=True)

        self._log_config()

    # ═══════════════════════════════════════════════
    #  Utilidades
    # ═══════════════════════════════════════════════

    def _get_env(self, key: str, default: Any = None) -> str:
        # Always return str type, not Union[str, None]
        value = os.getenv(key)
        if value is None:
            return str(default) if default is not None else ""
        return value

    def _get_validated(self, key: str, default: Any, valid_values: list, cast_to: type = str) -> Any:
        value = self._get_env(key, default)
        try:
            value_casted = cast_to(value)
            if value_casted in valid_values:
                return value_casted
        except (ValueError, TypeError):
            pass
        logger.warning(f"⚠ Valor no válido para '{key}': {value}. Usando predeterminado: {default}")
        return default

    from typing import Optional

    def _get_float(
        self,
        key: str,
        default: float,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
        positive: bool = False
    ) -> float:
        value_str = self._get_env(key, default)
        try:
            value = float(value_str)
            if positive and value < 0:
                raise ValueError
            if min_value is not None and value < min_value:
                raise ValueError
            if max_value is not None and value > max_value:
                raise ValueError
            return value
        except (ValueError, TypeError):
            logger.warning(f"⚠ '{key}' no es válido. Usando valor predeterminado: {default}")
            return float(default)
       

    def _log_config(self):
        logger.info(f"✅ Configuración cargada: {self.to_dict()}")

    def update_config(self, key: str, value: Any) -> bool:
        if not hasattr(self, key):
            logger.error(f"❌ Configuración '{key}' no es válida.")
            return False
        try:
            setattr(self, key, value)
            logger.info(f"✅ Configuración '{key}' actualizada: {value}")
            return True
        except Exception as e:
            logger.error(f"❌ Error al actualizar configuración '{key}': {e}")
            return False

    def to_dict(self) -> Dict[str, Any]:
        return {key: getattr(self, key) for key in vars(self)}