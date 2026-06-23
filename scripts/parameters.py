"""
parameters.py
=============

Centralized, elegant submodule for all parameters, keys, combinations, and indicator definitions.

This is the single source of truth for:
- Configuration defaults and validation
- API key and secret access (safe, with clear errors)
- Metric weights, thresholds, risk parameters
- Indicator registry (inspired by professional exchange formulas modules)
- Code combinations and signal logic parameters

Design goals (high-quality, artistic code):
- Dataclasses for immutable, clear structures
- Declarative IndicatorDef (key, name, formula description, compute)
- No scattered hardcodes
- PEP 8, type hints, minimal but expressive
- Easy to extend without touching engine code

No microservices — pure Python module.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

logger = logging.getLogger("NertzParameters")


# =============================================================================
# Core Types (elegant and explicit)
# =============================================================================

IndicatorValue = float | Dict[str, Any] | None


@dataclass(frozen=True)
class IndicatorDef:
    """Declarative definition of a trading indicator / metric.

    Mirrors professional patterns from high-quality exchange formula libraries:
    - key: stable identifier (used in weights, logs, features)
    - name: human readable
    - formula: short mathematical description (for docs / prompts / snapshots)
    - fields: data fields this indicator needs from snapshot
    - compute: pure function(snapshot_like) -> value
    - category, description, output_range for documentation and UI
    """
    key: str
    name: str
    formula: str
    fields: List[str]
    compute: Callable[[Any], IndicatorValue]
    category: str = "signal"
    description: str = ""
    output_range: Optional[Tuple[float, float]] = None


@dataclass(frozen=True)
class RiskParams:
    capital_usdt: float = 5000.0
    fee_rate: float = 0.002
    risk_factor: float = 0.01
    max_position_pct: float = 0.10
    min_trade_size: float = 0.001
    max_trade_size: float = 100.0


@dataclass(frozen=True)
class Thresholds:
    egm_buy: float = 0.5
    egm_sell: float = -0.5
    combined_buy: float = 1.0
    combined_sell: float = -1.0
    pio: float = 0.1


@dataclass(frozen=True)
class MetricWeights:
    # Must sum to ~1.0
    egm: float = 0.20
    ild: float = 0.30
    rol: float = 0.30
    pio: float = 0.10
    ogm: float = 0.10


@dataclass(frozen=True)
class TimingParams:
    default_sleep_time: int = 60
    kline_stale_ms: int = 60_000
    ticker_trade_cooldown_s: int = 5
    orderbook_log_interval_s: int = 5
    balance_sync_interval_s: int = 15


@dataclass(frozen=True)
class ExecutionParams:
    order_max_retries: int = 3
    order_retry_base_delay_s: float = 2.0
    order_max_backoff_s: float = 8.0
    http_timeout_s: int = 10
    ws_reconnect_delay_s: int = 5
    ws_max_reconnect_attempts: int = 5


# =============================================================================
# Indicator Registry (the heart of clean formula management)
# =============================================================================

# These compute functions are defined later in formulas.py and injected
# We declare the registry here so parameters is the single place to see all indicators + their metadata.

INDICATOR_REGISTRY: Dict[str, IndicatorDef] = {}


def register_indicator(ind: IndicatorDef) -> None:
    """Central registration point. Call from formulas module at import time."""
    if ind.key in INDICATOR_REGISTRY:
        logger.warning(f"Overwriting existing indicator definition: {ind.key}")
    INDICATOR_REGISTRY[ind.key] = ind


def get_indicator(key: str) -> IndicatorDef:
    if key not in INDICATOR_REGISTRY:
        raise KeyError(f"Unknown indicator: {key}. Available: {list(INDICATOR_REGISTRY)}")
    return INDICATOR_REGISTRY[key]


def list_indicators(category: Optional[str] = None) -> List[IndicatorDef]:
    items = list(INDICATOR_REGISTRY.values())
    if category:
        items = [i for i in items if i.category == category]
    return items


# =============================================================================
# Combinations (signal logic, risk combos, feature groups)
# =============================================================================

SIGNAL_COMBINATIONS = {
    "primary": ["egm", "ild", "rol"],
    "confirmation": ["pio", "ogm"],
    "full": ["egm", "ild", "rol", "pio", "ogm"],
}

XGB_FEATURE_COMBINATION = SIGNAL_COMBINATIONS["full"] + ["volatility", "price_change_5", "volume_ratio"]

RISK_COMBINATIONS = {
    "conservative": {"risk_factor": 0.005, "max_position_pct": 0.05},
    "standard": {"risk_factor": 0.01, "max_position_pct": 0.10},
    "aggressive": {"risk_factor": 0.02, "max_position_pct": 0.15},
}


# =============================================================================
# Centralized Configuration (unified, beautiful)
# =============================================================================

class Config:
    """Professional, single source of configuration.

    All magic numbers, weights, thresholds, and combinations live here or in the registry.
    """

    # Environment
    SYMBOL: str = os.getenv("SYMBOL", "BTCUSDT")
    TIMEFRAME: str = os.getenv("TIMEFRAME", "1m")
    BYBIT_ENV: str = os.getenv("BYBIT_ENV", "demo")

    # Risk & Sizing
    RISK = RiskParams(
        capital_usdt=float(os.getenv("CAPITAL_USDT", 5000.0)),
        fee_rate=float(os.getenv("FEE_RATE", 0.002)),
        risk_factor=float(os.getenv("RISK_FACTOR", 0.01)),
        max_position_pct=float(os.getenv("MAX_POSITION_PCT", 0.10)),
        min_trade_size=float(os.getenv("MIN_TRADE_SIZE", 0.001)),
        max_trade_size=float(os.getenv("MAX_TRADE_SIZE", 100.0)),
    )

    # Thresholds
    THRESHOLDS = Thresholds(
        egm_buy=float(os.getenv("EGM_BUY_THRESHOLD", 0.5)),
        egm_sell=float(os.getenv("EGM_SELL_THRESHOLD", -0.5)),
    )

    # Metrics
    WEIGHTS = MetricWeights(
        egm=float(os.getenv("WEIGHT_EGM", 0.20)),
        ild=float(os.getenv("WEIGHT_ILD", 0.30)),
        rol=float(os.getenv("WEIGHT_ROL", 0.30)),
        pio=float(os.getenv("WEIGHT_PIO", 0.10)),
        ogm=float(os.getenv("WEIGHT_OGM", 0.10)),
    )

    COMBINED_SCALE: float = float(os.getenv("COMBINED_SCALE", 10.0))
    COMBINED_CLAMP: float = float(os.getenv("COMBINED_CLAMP", 10.0))
    MIN_PRICE_RANGE_PCT: float = float(os.getenv("MIN_PRICE_RANGE_PCT", 0.01))
    SPREAD_REFERENCE: float = float(os.getenv("SPREAD_REFERENCE", 0.02))
    EPSILON: float = 1e-8

    # Advanced scaling parameters (enriched from TSM Exchange pipeline experiments)
    ROL_SCALE: float = float(os.getenv("ROL_SCALE", 2.8))
    OGM_SCALE: float = float(os.getenv("OGM_SCALE", 1.15))
    PRICE_SHIFT_FACTOR: float = float(os.getenv("PRICE_SHIFT_FACTOR", 8.0))

    # Validated combined weight variations (from running our params on TSM reference)
    COMBINED_VARIATIONS = {
        "balanced": {"ild": 0.30, "egm": 0.20, "rol": 0.30, "pio": 0.10, "ogm": 0.10},
        "tsm_inspired": {"ild": 0.25, "egm": 0.40, "rol": 0.30, "pio": 0.05, "ogm": 0.00},  # heavy on egm
        "flow_heavy": {"ild": 0.35, "egm": 0.15, "rol": 0.25, "pio": 0.15, "ogm": 0.10},
    }

    # Timing & Execution
    TIMING = TimingParams()
    EXEC = ExecutionParams()

    # Order / TP SL base (can be overridden by dynamic logic)
    TP_PERCENTAGE: float = float(os.getenv("TP_PERCENTAGE", 0.02))
    SL_PERCENTAGE: float = float(os.getenv("SL_PERCENTAGE", 0.01))

    # Validations
    VALID_SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT"]
    VALID_TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]

    @staticmethod
    def get_api_keys() -> tuple[str, str]:
        """Safe, centralized key retrieval with excellent error messages."""
        key = os.getenv("BYBIT_API_KEY", "")
        secret = os.getenv("BYBIT_API_SECRET", "")
        if not key or not secret or key.startswith("your_"):
            raise RuntimeError(
                "BYBIT_API_KEY and BYBIT_API_SECRET must be set in environment.\n"
                "For demo/public data only, you can run without keys (read-only mode)."
            )
        return key, secret

    @classmethod
    def as_dict(cls) -> Dict[str, Any]:
        """Export for logging, snapshots, and UI."""
        return {
            "symbol": cls.SYMBOL,
            "timeframe": cls.TIMEFRAME,
            "bybit_env": cls.BYBIT_ENV,
            "risk": cls.RISK.__dict__,
            "thresholds": cls.THRESHOLDS.__dict__,
            "weights": cls.WEIGHTS.__dict__,
            "combined_scale": cls.COMBINED_SCALE,
            "timing": cls.TIMING.__dict__,
        }


# =============================================================================
# Convenience re-exports (so other modules import from one place)
# =============================================================================

get_config = Config
register_indicator = register_indicator
get_indicator = get_indicator
list_indicators = list_indicators

# Default weights exposed for backward compatibility during transition
DEFAULT_WEIGHTS = {
    "egm": Config.WEIGHTS.egm,
    "ild": Config.WEIGHTS.ild,
    "rol": Config.WEIGHTS.rol,
    "pio": Config.WEIGHTS.pio,
    "ogm": Config.WEIGHTS.ogm,
}
