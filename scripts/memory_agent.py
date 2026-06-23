import logging
from typing import List, Dict, Any

logger = logging.getLogger("NertzMemoryAgent")


class QwenMemoryAgent:
    """Professional memory agent. Pure summarizer for closed trade history.

    Expects the caller (engine) to pass its authoritative list of trades.
    Designed for clean composition and easy subclassing/override.
    """

    def get_recent_context(
        self, trades: List[Dict[str, Any]], symbol: str = "", limit: int = 10
    ) -> str:
        """Build concise performance context string from closed trades list."""
        if not trades:
            return f"No hay historial de operaciones cerradas para {symbol}."

        recent = self._get_recent_trades(trades, limit)
        if not recent:
            return f"No hay trades recientes para {symbol}."

        stats = self._compute_stats(recent)
        streak = self._compute_streak(recent)
        details = self._format_trade_details(recent[:5])

        return self._format_context(symbol, len(recent), stats, streak, details)

    # --- Small focused helpers (override-friendly) ---

    def _get_recent_trades(
        self, trades: List[Dict[str, Any]], limit: int
    ) -> List[Dict[str, Any]]:
        """Return up to `limit` most recent trades (newest first)."""
        def ts_key(t: Dict) -> str:
            return str(t.get("timestamp", ""))

        sorted_trades = sorted(trades, key=ts_key, reverse=True)
        return sorted_trades[:limit]

    def _compute_stats(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate key performance metrics for the window."""
        if not trades:
            return {"win_rate": 0.0, "pnl": 0.0, "wins": 0, "losses": 0}

        wins = [t for t in trades if t.get("profit_loss", 0) > 0]
        losses = [t for t in trades if t.get("profit_loss", 0) < 0]
        total = len(trades)

        win_pnl = sum(t.get("profit_loss", 0) for t in wins)
        loss_pnl = sum(t.get("profit_loss", 0) for t in losses)
        net = win_pnl + loss_pnl

        win_rate = (len(wins) / total) * 100 if total else 0.0
        avg_win = win_pnl / len(wins) if wins else 0.0
        avg_loss = loss_pnl / len(losses) if losses else 0.0

        pf = win_pnl / abs(loss_pnl) if loss_pnl < 0 else (float("inf") if win_pnl > 0 else 0.0)

        return {
            "win_rate": round(win_rate, 1),
            "pnl": round(net, 2),
            "wins": len(wins),
            "losses": len(losses),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": pf if pf != float("inf") else "∞",
        }

    def _compute_streak(self, trades: List[Dict[str, Any]]) -> str:
        """Detect current win/loss streak from most recent trades."""
        streak_type = None
        count = 0
        for t in trades:
            pnl = t.get("profit_loss", 0)
            if pnl > 0:
                ctype = "ganancias"
            elif pnl < 0:
                ctype = "pérdidas"
            else:
                continue

            if streak_type is None:
                streak_type, count = ctype, 1
            elif streak_type == ctype:
                count += 1
            else:
                break
        return f"Racha actual: {count} trades en {streak_type}" if streak_type else "Sin racha definida"

    def _format_trade_details(self, trades: List[Dict[str, Any]]) -> str:
        """Format compact list of recent individual trades."""
        lines = []
        for t in trades:
            action = str(t.get("action", "")).upper()
            ep = t.get("entry_price")
            ex = t.get("exit_price")
            pnl = t.get("profit_loss", 0)
            ts = t.get("timestamp", "")

            ep_s = f"{ep:.2f}" if isinstance(ep, (int, float)) else "?"
            ex_s = f"{ex:.2f}" if isinstance(ex, (int, float)) else "?"

            lines.append(f"- {action} @ {ep_s} → {ex_s} | P&L: {pnl:+.2f} | {ts}")
        return "\n".join(lines) if lines else "  (sin detalle)"

    def _format_context(
        self, symbol: str, count: int, stats: Dict, streak: str, details: str
    ) -> str:
        """Assemble final clean context string."""
        win_count = stats["wins"]
        loss_count = stats["losses"]
        return (
            f"Historial de operaciones cerradas del bot para {symbol}:\n"
            f"- Trades en ventana: {count}\n"
            f"- Win Rate: {stats['win_rate']}% ({win_count}W / {loss_count}L)\n"
            f"- P&L neto: {stats['pnl']:+.2f} USDT | Avg Win: {stats['avg_win']:+.2f} | Avg Loss: {stats['avg_loss']:+.2f}\n"
            f"- Profit Factor: {stats['profit_factor']}\n"
            f"- {streak}\n"
            f"Últimos trades:\n{details}"
        )

    # --- Prediction snapshot support (for memory MCP validation) ---

    def record_prediction_snapshot(
        self, symbol: str, prediction: Dict[str, Any], features: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create a snapshot suitable for add_observations in the memory knowledge graph.

        Later, when outcome is known, add another observation with actual result.
        """
        from datetime import datetime, timezone

        snap = {
            "entityName": f"{symbol}_pred_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            "prediction": prediction,
            "features": features,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "outcome": None,  # fill on trade close
        }
        logger.info(f"Prediction snapshot prepared for {symbol}")
        return snap

    def attach_outcome_to_snapshot(self, snapshot: Dict[str, Any], realized_pnl: float, hit: str) -> Dict[str, Any]:
        """Attach the actual outcome. Use contents for memory MCP add_observations."""
        snapshot["outcome"] = {
            "realized_pnl": round(realized_pnl, 4),
            "result": hit,
        }
        return snapshot
