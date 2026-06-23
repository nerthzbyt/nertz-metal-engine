import os
import logging
from typing import Dict, Any, List, Optional, Tuple
import numpy as np

logger = logging.getLogger("NertzXGBPredictor")

try:
    import xgboost as xgb
    from sklearn.model_selection import train_test_split
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False
    logger.warning("xgboost/scikit-learn missing — XGBPredictor falls back to heuristic.")


class XGBPredictor:
    """Clean, data-driven XGBoost directional predictor.

    - Extracts features from recent candles + metrics.
    - Returns structured (action, prob_up, confidence, features).
    - Supports training from labeled snapshots.
    - Snapshots designed for memory-graph validation (add_observations).
    """

    FEATURE_NAMES = [
        "combined", "ild", "egm", "rol", "pio", "ogm",
        "volatility", "price_change_5", "volume_ratio"
    ]

    def __init__(self, model_path: Optional[str] = None):
        self.model: Optional["xgb.XGBClassifier"] = None
        self.model_path = model_path or os.path.join(
            os.path.dirname(__file__), "..", "data", "xgb_direction.json"
        )
        self._load_model()

    def _load_model(self) -> None:
        if not XGB_AVAILABLE or not os.path.exists(self.model_path):
            return
        try:
            self.model = xgb.XGBClassifier()
            self.model.load_model(self.model_path)
            logger.info(f"XGB model loaded: {self.model_path}")
        except Exception as exc:
            logger.warning(f"XGB load failed: {exc}")
            self.model = None

    def _extract_features(self, candles: List[Dict[str, Any]], metrics: Dict[str, float]) -> np.ndarray:
        """Build 1D feature vector. Always returns valid shape."""
        if not candles or len(candles) < 5:
            base = [float(metrics.get(k, 0.0)) for k in self.FEATURE_NAMES[:7]]
            return np.array(base + [0.0, 0.0], dtype=np.float32).reshape(1, -1)

        closes = np.asarray([float(c.get("close", c.get("c", 0))) for c in candles[-5:]], dtype=np.float64)
        vols = np.asarray([float(c.get("volume", c.get("v", 0))) for c in candles[-5:]], dtype=np.float64)

        price_chg = (closes[-1] - closes[0]) / (closes[0] + 1e-12)
        vol_ratio = vols[-1] / (np.mean(vols[:-1]) + 1e-12) if len(vols) > 1 else 1.0

        vec = [
            float(metrics.get("combined", 0)),
            float(metrics.get("ild", 0)),
            float(metrics.get("egm", 0)),
            float(metrics.get("rol", 0)),
            float(metrics.get("pio", 0)),
            float(metrics.get("ogm", 0)),
            float(metrics.get("volatility", 0)),
            price_chg,
            vol_ratio,
        ]
        return np.array(vec, dtype=np.float32).reshape(1, -1)

    def predict_with_features(self, candles: List[Dict[str, Any]], metrics: Dict[str, float]) -> Dict[str, Any]:
        """Primary API: returns prediction + exact features for snapshotting."""
        feats = self._extract_features(candles, metrics)
        prob = self._predict_proba(feats, metrics)

        if prob > 0.55:
            action = "buy"
        elif prob < 0.45:
            action = "sell"
        else:
            action = "hold"

        conf = float(abs(prob - 0.5) * 2)

        feat_dict = {name: float(feats[0, i]) for i, name in enumerate(self.FEATURE_NAMES)}

        return {
            "action": action,
            "prob_up": round(prob, 4),
            "confidence": round(conf, 4),
            "features": feat_dict,
            "model": "xgb" if self.model is not None and XGB_AVAILABLE else "heuristic",
        }

    def _predict_proba(self, features: np.ndarray, metrics: Dict[str, float]) -> float:
        if self.model is not None and XGB_AVAILABLE:
            try:
                proba = self.model.predict_proba(features)[0]
                idx = 1 if 1 in getattr(self.model, "classes_", []) else -1
                return float(proba[idx])
            except Exception as exc:
                logger.debug(f"XGB inference error: {exc}")

        # Elegant fallback
        combined = float(metrics.get("combined", 0.0))
        prob = 0.5 + np.clip(combined / 20.0, -0.4, 0.4)
        return float(np.clip(prob, 0.1, 0.9))

    def fit_from_snapshots(self, snapshots: List[Dict[str, Any]], save: bool = True) -> None:
        """Train from list of snapshots that have 'features' + later 'outcome.realized_pnl'."""
        if not XGB_AVAILABLE:
            return

        X, y = [], []
        for snap in snapshots:
            feat = snap.get("features") or snap.get("prediction", {}).get("features", {})
            if not feat:
                continue
            vec = [float(feat.get(k, 0)) for k in self.FEATURE_NAMES]
            pnl = snap.get("outcome", {}).get("realized_pnl", 0) if snap.get("outcome") else 0
            X.append(vec)
            y.append(1 if pnl > 0 else 0)

        if len(X) < 30:
            logger.info("Too few labeled snapshots for XGB training")
            return

        Xa, ya = np.array(X), np.array(y)
        Xtr, Xv, ytr, yv = train_test_split(Xa, ya, test_size=0.2, random_state=42)

        model = xgb.XGBClassifier(
            n_estimators=120, max_depth=4, learning_rate=0.05,
            eval_metric="logloss", early_stopping_rounds=15,
        )
        model.fit(Xtr, ytr, eval_set=[(Xv, yv)], verbose=False)

        self.model = model
        if save:
            os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
            model.save_model(self.model_path)
            logger.info(f"XGB retrained from snapshots → {self.model_path}")

    def make_snapshot(self, symbol: str, prediction: Dict[str, Any]) -> Dict[str, Any]:
        """Return a clean snapshot record ready for memory_agent or add_observations MCP."""
        from datetime import datetime, timezone
        return {
            "entityName": f"{symbol}_xgb_pred_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
            "contents": [
                f"prob_up={prediction.get('prob_up')}",
                f"action={prediction.get('action')}",
                f"features={prediction.get('features')}",
                f"model={prediction.get('model')}"
            ],
        }
