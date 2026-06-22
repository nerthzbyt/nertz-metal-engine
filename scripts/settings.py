import os
import logging
from typing import Any, List, Dict, Union

logger = logging.getLogger ("NertzMetalEngine")


class ConfigSettings:
    # Valores por defecto y parámetros válidos para configuración
    DEFAULTS = {
        "VALID_SYMBOLS": ["BTCUSDT", "ETHUSDT", "XRPUSDT"],
        "VALID_TIMEFRAMES": ["1m", "5m", "15m", "1h", "4h", "1d"],
        "VALID_ORDER_TYPES": ["limit", "market", "stop"],
        "VALID_TIME_IN_FORCE": ["GoodTillCancel", "ImmediateOrCancel", "FillOrKill"],
        "VALID_ORDERBOOK_DEPTHS": [1, 5, 10, 25],
        "DEFAULT_ORDERBOOK_DEPTH": 5,
        "VALID_BYBIT_ENV": ["demo", "live"],
        "DEFAULT_BYBIT_ENV": "demo",
        "CAPITAL_USDT": 5000.0,
        "FEE_RATE": 0.002,
        "RISK_FACTOR": 0.01,
        "TP_PERCENTAGE": 0.02,
        "SL_PERCENTAGE": 0.01,
        "MIN_TRADE_SIZE": 0.001,
        "MAX_TRADE_SIZE": 100.0,
        "MAX_ITERATIONS": 1000,
        "EGM_BUY_THRESHOLD": 0.5,
        "EGM_SELL_THRESHOLD": -0.5,
        "PIO_THRESHOLD": 0.1,
        "DEFAULT_SLEEP_TIME": 60,  # Segundos
    }

    def __init__ (self):
        # Asignar configuraciones iniciales desde entorno o usar valores predeterminados
        self.BYBIT_API_KEY = self._get_env ("BYBIT_API_KEY")
        self.BYBIT_API_SECRET = self._get_env ("BYBIT_API_SECRET")
        # Reemplazar USE_TESTNET con BYBIT_ENV
        self.BYBIT_ENV = self._get_validated (
            "BYBIT_ENV", self.DEFAULTS ["DEFAULT_BYBIT_ENV"], self.DEFAULTS ["VALID_BYBIT_ENV"]
        )

        # Cargar configuraciones dinámicas con validación
        self.SYMBOL = self._get_validated (
            "SYMBOL", "BTCUSDT", self.DEFAULTS ["VALID_SYMBOLS"]
        )
        self.TIMEFRAME = self._get_validated (
            "TIMEFRAME", "1m", self.DEFAULTS ["VALID_TIMEFRAMES"]
        )
        self.ORDER_TYPE = self._get_validated (
            "ORDER_TYPE", "limit", self.DEFAULTS ["VALID_ORDER_TYPES"]
        )
        self.TIME_IN_FORCE = self._get_validated (
            "TIME_IN_FORCE", "GoodTillCancel", self.DEFAULTS ["VALID_TIME_IN_FORCE"]
        )
        self.ORDERBOOK_DEPTH = self._get_validated (
            "ORDERBOOK_DEPTH",
            self.DEFAULTS ["DEFAULT_ORDERBOOK_DEPTH"],
            self.DEFAULTS ["VALID_ORDERBOOK_DEPTHS"],
            cast_to=int
        )

        # Manejar atributos numéricos
        self.CAPITAL_USDT = self._get_float ("CAPITAL_USDT", self.DEFAULTS ["CAPITAL_USDT"], positive=True)
        self.FEE_RATE = self._get_float ("FEE_RATE", self.DEFAULTS ["FEE_RATE"], min_value=0.0, max_value=0.1)
        self.RISK_FACTOR = self._get_float ("RISK_FACTOR", self.DEFAULTS ["RISK_FACTOR"], min_value=0.0, max_value=1.0)
        self.TP_PERCENTAGE = self._get_float ("TP_PERCENTAGE", self.DEFAULTS ["TP_PERCENTAGE"])
        self.SL_PERCENTAGE = self._get_float ("SL_PERCENTAGE", self.DEFAULTS ["SL_PERCENTAGE"])
        self.MIN_TRADE_SIZE = self._get_float ("MIN_TRADE_SIZE", self.DEFAULTS ["MIN_TRADE_SIZE"])
        self.MAX_TRADE_SIZE = self._get_float ("MAX_TRADE_SIZE", self.DEFAULTS ["MAX_TRADE_SIZE"])
        self.MAX_ITERATIONS = self._get_float ("MAX_ITERATIONS", self.DEFAULTS ["MAX_ITERATIONS"])
        self.DEFAULT_SLEEP_TIME = self._get_float ("DEFAULT_SLEEP_TIME", self.DEFAULTS ["DEFAULT_SLEEP_TIME"],
                                                   positive=True)

        self.EGM_BUY_THRESHOLD = self._get_float (
            "EGM_BUY_THRESHOLD", self.DEFAULTS ["EGM_BUY_THRESHOLD"]
        )
        self.EGM_SELL_THRESHOLD = self._get_float (
            "EGM_SELL_THRESHOLD", self.DEFAULTS ["EGM_SELL_THRESHOLD"]
        )
        self.PIO_THRESHOLD = self._get_float ("PIO_THRESHOLD", self.DEFAULTS ["PIO_THRESHOLD"])

        # Log de la configuración cargada
        self._log_config ()

    # =====================================
    # Métodos Utilitarios
    # =====================================

    def _get_env (self, key: str, default: Any = None) -> Union [str, None]:
        return os.getenv (key, default)

    def _get_env_bool (self, key: str, default: bool = False) -> bool:
        value = self._get_env (key, str (default))
        return value.strip ().lower () in ["true", "1", "yes"]

    def _get_validated (self, key: str, default: Any, valid_values: list, cast_to: type = str) -> Any:
        """Validar configuración según valores permitidos."""
        value = self._get_env (key, default)
        try:
            value_casted = cast_to (value)
            if value_casted in valid_values:
                return value_casted
        except (ValueError, TypeError):
            pass
        logger.warning (f"⚠ Valor no válido para '{key}': {value}. Usando predeterminado: {default}")
        return default

    def _get_float (self, key: str, default: float, min_value: float = None, max_value: float = None,
                    positive: bool = False) -> float:
        """Validar valores numéricos."""
        value = self._get_env (key, default)
        try:
            value = float (value)
            if positive and value < 0:
                raise ValueError
            if min_value is not None and value < min_value:
                raise ValueError
            if max_value is not None and value > max_value:
                raise ValueError
            return value
        except ValueError:
            logger.warning (
                f"⚠ '{key}' no es válido. Usando valor predeterminado: {default}"
            )
            return default

    def _log_config (self):
        """Registrar configuraciones cargadas."""
        logger.info (f"✅ Configuración cargada: {self.to_dict ()}")

    # =====================================
    # Métodos Públicos para la API
    # =====================================

    def update_config (self, key: str, value: Any) -> bool:
        """Actualizar configuración dinámicamente si la clave existe."""
        if not hasattr (self, key):
            logger.error (f"❌ Configuración '{key}' no es válida.")
            return False
        try:
            setattr (self, key, value)
            logger.info (f"✅ Configuración '{key}' actualizada: {value}")
            return True
        except Exception as e:
            logger.error (f"❌ Error al actualizar configuración '{key}': {e}")
            return False

    def to_dict (self) -> Dict [str, Any]:
        """Convertir configuraciones a un dict serializable."""
        return {key: getattr (self, key) for key in vars (self)}