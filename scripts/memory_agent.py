import sqlite3
import os
import logging
from typing import List, Dict, Any

logger = logging.getLogger("NertzMemoryAgent")

class QwenMemoryAgent:
    def __init__(self, db_path: str = None):
        if db_path is None:
            # Ruta predeterminada relativa al script
            base_dir = os.path.dirname(os.path.abspath(__file__))
            self.db_path = os.path.join(base_dir, "..", "data", "trading.db")
        else:
            self.db_path = db_path

    def get_recent_context(self, symbol: str, limit: int = 10) -> str:
        if not os.path.exists(self.db_path):
            return "No hay historial de trades recientes en la base de datos (archivo DB inexistente)."

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Consultar los últimos trades realizados
            cursor.execute(
                "SELECT action, entry_price, exit_price, profit_loss, timestamp FROM trades WHERE symbol = ? ORDER BY timestamp DESC LIMIT ?",
                (symbol, limit)
            )
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                return f"No hay trades recientes registrados para {symbol}."

            trades = [dict(row) for row in rows]
            total_trades = len(trades)
            winning_trades = sum(1 for t in trades if t["profit_loss"] > 0)
            losing_trades = sum(1 for t in trades if t["profit_loss"] < 0)
            win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0.0
            total_pnl = sum(t["profit_loss"] for t in trades)

            # Determinar racha reciente
            streak_type = None
            streak_count = 0
            for t in trades:
                pnl = t["profit_loss"]
                if pnl > 0:
                    current_type = "ganancias (wins)"
                elif pnl < 0:
                    current_type = "pérdidas (losses)"
                else:
                    continue
                
                if streak_type is None:
                    streak_type = current_type
                    streak_count = 1
                elif streak_type == current_type:
                    streak_count += 1
                else:
                    break

            streak_desc = f"Racha actual: {streak_count} trades en {streak_type}" if streak_type else "Sin racha definida"

            # Formatear últimos 5 trades en detalle
            trade_list_str = []
            for t in trades[:5]:
                trade_list_str.append(
                    f"- {t['action'].upper()} @ {t['entry_price']:.2f} | Salida @ {t['exit_price']:.2f} | P&L: {t['profit_loss']:.2f} USDT | Fecha: {t['timestamp']}"
                )

            summary = (
                f"Resumen de los últimos {total_trades} trades para {symbol}:\n"
                f"- Win Rate: {win_rate:.2f}%\n"
                f"- P&L acumulado en ventana de trades: {total_pnl:.2f} USDT\n"
                f"- {streak_desc}\n"
                f"Detalle de los últimos trades:\n"
                + "\n".join(trade_list_str)
            )
            return summary
        except Exception as e:
            logger.error(f"Error al consultar base de datos en QwenMemoryAgent: {e}", exc_info=True)
            return f"Error al consultar el historial de trades: {str(e)}"
