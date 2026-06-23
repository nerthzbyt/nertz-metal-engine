"""
intelligence.py
===============

Elegant, unified intelligence layer.

Consolidates:
- Qwen LLM signal validation
- XGBoost probabilistic prediction
- Memory / historical context

Designed to be beautiful, testable, and easy to reason about.
All heavy logic is delegated to formulas.py and parameters.py.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import aiohttp
from aiohttp import ClientTimeout

from .parameters import Config
from .formulas import extract_xgb_features

logger = logging.getLogger("NertzIntelligence")


# =============================================================================
# XGBoost Predictor (light, elegant wrapper)
# =============================================================================

try:
    import xgboost as xgb
    from sklearn.model_selection import train_test_split
    XGB_OK = True
except ImportError:
    XGB_OK = False


class XGBPredictor:
    """Professional thin wrapper around XGBoost for direction probability."""

    def __init__(self, model_path: Optional[str] = None):
        self.model = None
        self.model_path = model_path or os.path.join(
            os.path.dirname(__file__), "..", "data", "xgb_direction.json"
        )
        self._try_load()

    def _try_load(self) -> None:
        if not XGB_OK or not os.path.exists(self.model_path):
            return
        try:
            self.model = xgb.XGBClassifier()
            self.model.load_model(self.model_path)
        except Exception as e:
            logger.warning(f"Could not load XGB model: {e}")

    def predict(self, candles: List[Dict], metrics: Dict[str, float]) -> Dict[str, Any]:
        feats = extract_xgb_features(candles, metrics)
        feat_vec = [feats.get(k, 0.0) for k in [
            "combined", "ild", "egm", "rol", "pio", "ogm", "volatility", "price_change_5", "volume_ratio"
        ]]

        if self.model is not None and XGB_OK:
            try:
                proba = self.model.predict_proba([feat_vec])[0]
                up = float(proba[1] if len(proba) > 1 else proba[0])
            except Exception:
                up = 0.5 + (metrics.get("combined", 0) / 20.0)
        else:
            up = 0.5 + min(max(metrics.get("combined", 0) / 20.0, -0.4), 0.4)

        prob = max(0.05, min(0.95, up))
        action = "buy" if prob > 0.55 else ("sell" if prob < 0.45 else "hold")

        return {
            "action": action,
            "prob_up": round(prob, 4),
            "confidence": round(abs(prob - 0.5) * 2, 4),
            "features": feats,
            "model": "xgb" if self.model else "heuristic",
        }


# =============================================================================
# Qwen Signal Validator (clean, professional)
# =============================================================================

class QwenValidator:
    """LLM-based signal validator with excellent prompt hygiene."""

    def __init__(self, api_key: Optional[str] = None, model: str = "qwen-plus"):
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY", "")
        self.model = model
        self.endpoint = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

    async def validate(
        self,
        symbol: str,
        proposed: str,
        metrics: Dict[str, float],
        history_context: str,
        xgb: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        proposed = proposed.lower()

        if not self.api_key or self.api_key.startswith("your_"):
            return self._heuristic_fallback(proposed, metrics, xgb)

        prompt = self._build_prompt(symbol, proposed, metrics, history_context, xgb)

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are an expert quantitative trading signal validator. "
                               "Respond ONLY with valid JSON: {\"action\": \"buy\"|\"sell\"|\"hold\", \"confidence\": 0-1, \"reason\": \"...\"}"
                },
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,
        }

        try:
            timeout = ClientTimeout(total=12)
            async with aiohttp.ClientSession() as sess:
                async with sess.post(
                    self.endpoint,
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=timeout,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        content = data["choices"][0]["message"]["content"].strip()
                        if content.startswith("```"):
                            content = "\n".join(content.splitlines()[1:-1]).strip() if "```" in content else content
                        result = json.loads(content)
                        result["action"] = result.get("action", "hold").lower()
                        return result
        except Exception as e:
            logger.warning(f"Qwen call failed: {e}")

        return self._heuristic_fallback(proposed, metrics, xgb)

    def _build_prompt(self, symbol, proposed, metrics, history, xgb):
        xgb_line = ""
        if xgb:
            xgb_line = f"XGBoost says: {xgb.get('action')} (prob_up={xgb.get('prob_up')})\n"

        return (
            f"Symbol: {symbol}\n"
            f"Local proposal: {proposed.upper()}\n\n"
            f"Current metrics:\n"
            f"  combined={metrics.get('combined', 0):.3f} ild={metrics.get('ild', 0):.3f} "
            f"egm={metrics.get('egm', 0):.3f} rol={metrics.get('rol', 0):.3f}\n\n"
            f"{xgb_line}"
            f"Historical performance:\n{history}\n\n"
            "Decide: confirm, flip, or hold? Return strict JSON only."
        )

    def _heuristic_fallback(self, proposed: str, metrics: Dict, xgb: Optional[Dict]) -> Dict[str, Any]:
        combined = metrics.get("combined", 0.0)
        if xgb and xgb.get("action") == proposed:
            return {"action": proposed, "confidence": 0.78, "reason": "XGB + local agreement (heuristic)"}

        if proposed == "buy" and combined >= 0.8:
            return {"action": "buy", "confidence": 0.72, "reason": "Heuristic: combined supports long"}
        if proposed == "sell" and combined <= -0.8:
            return {"action": "sell", "confidence": 0.72, "reason": "Heuristic: combined supports short"}
        return {"action": "hold", "confidence": 0.65, "reason": "Heuristic fallback to hold"}


# =============================================================================
# Memory Context Provider (slim and focused)
# =============================================================================

class MemoryContext:
    """Lightweight historical context provider.

    In a full system this would talk to the memory MCP or a proper store.
    Here we keep it elegant and injectable.
    """

    def __init__(self, trades: Optional[List[Dict]] = None):
        self.trades = trades or []

    def get_recent_context(self, symbol: str, limit: int = 8) -> str:
        sym_trades = [t for t in self.trades if t.get("symbol") == symbol or not t.get("symbol")][-limit:]
        if not sym_trades:
            return f"No closed trades recorded yet for {symbol}."

        wins = [t for t in sym_trades if t.get("profit_loss", 0) > 0]
        wr = len(wins) / len(sym_trades) * 100 if sym_trades else 0
        pnl = sum(t.get("profit_loss", 0) for t in sym_trades)

        return (
            f"Recent {len(sym_trades)} trades | WinRate {wr:.1f}% | Net PnL {pnl:+.2f} USDT\n"
            f"Last few: " + ", ".join(f"{t.get('action','?')}:{t.get('profit_loss',0):+.0f}" for t in sym_trades[-3:])
        )


# =============================================================================
# Unified Intelligence Facade
# =============================================================================

class IntelligenceLayer:
    """One clean object the engine can talk to."""

    def __init__(self):
        self.xgb = XGBPredictor()
        self.qwen = QwenValidator()
        self.memory = MemoryContext()

    async def evaluate(
        self,
        symbol: str,
        local_action: str,
        metrics: Dict[str, float],
        candles: List[Dict],
        recent_trades: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        if recent_trades:
            self.memory.trades = recent_trades

        xgb_pred = self.xgb.predict(candles, metrics)
        history = self.memory.get_recent_context(symbol)

        validation = await self.qwen.validate(
            symbol, local_action, metrics, history, xgb=xgb_pred
        )

        return {
            "xgb": xgb_pred,
            "qwen": validation,
            "history_summary": history,
        }
