import os
import json
import logging
import aiohttp
from aiohttp import ClientTimeout
from typing import Dict, Any, Optional
from utils import (
    ConfigProtocol,
    BaseTradingStrategy,
    _cfg,
    _default_metrics,
    _empty_signal,
    calculate_metrics,
    calculate_tp_sl,
    evaluate_trend,
    save_results,
    set_config,
    timestamp_to_datetime,
)   
logger = logging.getLogger("NertzQwenAgent")

class QwenSignalAgent:
    def __init__(self, api_key: Optional[str] = None, model: str = "qwen-plus"):
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        self.model = model
        self.endpoint = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

    async def validate_signal(
        self,
        symbol: str,
        proposed_action: str,
        metrics: Dict[str, float],
        history_context: str,
        xgb_context: Optional[str] = None,
        extra_analysis: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Valida una señal propuesta utilizando Qwen Cloud o un fallback simulado.
        Retorna un dict con:
        - "action": "buy" | "sell" | "hold"
        - "confidence": float (0.0 a 1.0)
        - "reason": str
        """
        # Limpieza/Normalización de la acción propuesta
        proposed_action = proposed_action.lower()

        # Si no hay API key o es una de marcador de posición, usar fallback simulado
        if not self.api_key or self.api_key.startswith("your_") or len(self.api_key.strip()) == 0:
            logger.info("⚠️ DASHSCOPE_API_KEY no configurada o inválida. Usando fallback simulado para validación de Qwen.")
            return self._fallback_validation(symbol, proposed_action, metrics, history_context, xgb_context=xgb_context)

        prompt = self._build_prompt(symbol, proposed_action, metrics, history_context, xgb_context, extra_analysis)
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Eres un experto en trading. Valida señales usando métricas, historial, predicciones XGBoost y datos Bybit. "
                        "Responde SOLO JSON válido: {\"action\": \"buy\"|\"sell\"|\"hold\", \"confidence\": 0-1, \"reason\": \"explicación\"}"
                    )
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.1
        }

        try:
            timeout = ClientTimeout(total=10)
            async with aiohttp.ClientSession() as session:
                async with session.post(self.endpoint, headers=headers, json=payload, timeout=timeout) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        content = data["choices"][0]["message"]["content"].strip()
                        # Intentar limpiar posibles formatos markdown ```json ... ```
                        if content.startswith("```"):
                            lines = content.splitlines()
                            if lines[0].startswith("```"):
                                lines = lines[1:]
                            if lines and lines[-1].strip() == "```":
                                lines = lines[:-1]
                            content = "\n".join(lines).strip()
                        
                        result = json.loads(content)
                        # Validar claves requeridas
                        if "action" in result and "confidence" in result and "reason" in result:
                            result["action"] = result["action"].lower()
                            return result
                        else:
                            raise ValueError("JSON de respuesta incompleto")
                    else:
                        error_text = await resp.text()
                        logger.error(f"❌ Error en llamada a Qwen API ({resp.status}): {error_text}")
                        return self._fallback_validation(symbol, proposed_action, metrics, history_context, reason_prefix="[API Error Fallback] ", xgb_context=xgb_context)
        except Exception as e:
            logger.error(f"❌ Excepción durante llamada a Qwen API: {e}", exc_info=True)
            return self._fallback_validation(symbol, proposed_action, metrics, history_context, reason_prefix=f"[Exception Fallback: {type(e).__name__}] ", xgb_context=xgb_context)

    def _build_prompt(
        self,
        symbol: str,
        proposed_action: str,
        metrics: Dict[str, float],
        history_context: str,
        xgb_context: Optional[str] = None,
        extra_analysis: Optional[str] = None,
    ) -> str:
        parts = [
            f"Par de trading: {symbol}\n",
            f"Señal propuesta localmente: {proposed_action.upper()}\n\n",
            "Métricas del mercado actuales:\n",
            f"- EGM: {metrics.get('egm', 0.0):.4f}\n",
            f"- ILD: {metrics.get('ild', 0.0):.4f}\n",
            f"- ROL: {metrics.get('rol', 0.0):.4f}\n",
            f"- PIO: {metrics.get('pio', 0.0):.4f}\n",
            f"- OGM: {metrics.get('ogm', 0.0):.4f}\n",
            f"- Combined: {metrics.get('combined', 0.0):.4f}\n",
            f"- Volatilidad: {metrics.get('volatility', 0.0):.4f}\n\n",
        ]
        if xgb_context:
            parts.append(f"Predicción XGBoost (datos reales históricos):\n{xgb_context}\n\n")
        if extra_analysis:
            parts.append(f"Análisis adicional (Bybit data):\n{extra_analysis}\n\n")
        parts.append("Historial y Contexto de Trades Recientes:\n")
        parts.append(f"{history_context}\n\n")
        parts.append(
            "Analiza todo y responde JSON estricto: "
            '{"action": "buy"|"sell"|"hold", "confidence": float, "reason": "detallado"}'
        )
        return "".join(parts)

    def _fallback_validation(
        self,
        symbol: str,
        proposed_action: str,
        metrics: Dict[str, float],
        history_context: str,
        reason_prefix: str = "",
        xgb_context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Lógica de fallback que aprueba o rechaza la señal propuesta basada en heurísticas simples
        si la API de Qwen no está disponible o no se configuró la API key.
        """
        combined = metrics.get("combined", 0.0)
        volatility = metrics.get("volatility", 0.0)

        # Si hay alta volatilidad, somos más cautelosos
        if volatility > 0.05:
            action = "hold"
            reason = f"{reason_prefix}Filtro de volatilidad extrema ({volatility:.4f} > 0.05). Posición de precaución."
            confidence = 0.8
        elif proposed_action == "buy":
            # Validar si combined apoya la compra
            if combined >= 1.0:
                action = "buy"
                reason = f"{reason_prefix}Señal de compra validada heurísticamente. Combined ({combined:.2f}) apoya la tendencia alcista."
                confidence = 0.85
            else:
                action = "hold"
                reason = f"{reason_prefix}Compra denegada heurísticamente. Combined ({combined:.2f}) no es lo suficientemente alto (requiere >= 1.0)."
                confidence = 0.75
        elif proposed_action == "sell":
            # Validar si combined apoya la venta
            if combined <= -1.0:
                action = "sell"
                reason = f"{reason_prefix}Señal de venta validada heurísticamente. Combined ({combined:.2f}) apoya la tendencia bajista."
                confidence = 0.85
            else:
                action = "hold"
                reason = f"{reason_prefix}Venta denegada heurísticamente. Combined ({combined:.2f}) no es lo suficientemente bajo (requiere <= -1.0)."
                confidence = 0.75
        else:
            action = "hold"
            reason = f"{reason_prefix}Mantener (hold) confirmado por defecto."
            confidence = 0.9

        return {
            "action": action,
            "confidence": confidence,
            "reason": reason
        }
