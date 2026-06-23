"""NertzMetalEngine - Motor de trading cuantitativo para Bybit Spot (optimizado)."""

import asyncio
import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union

import aiohttp
import ntplib
import uvicorn
import websockets
from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from pybit.unified_trading import HTTP
from sqlalchemy.orm import Session
# DB setup now in models.py ; only Session needed here for type hints and deps

# ──────────────────────────────────────────────────────────────────────────────
# Local imports (package-aware + direct-run fallback for professional structure)
# ──────────────────────────────────────────────────────────────────────────────
try:
    # Preferred: when run as module (python -m scripts.nertz or uvicorn scripts.nertz:app)
    from . import utils
    from .parameters import Config, get_indicator
    from .formulas import calculate_metrics, build_rich_metrics_snapshot
    from .intelligence import IntelligenceLayer
    from .settings import ConfigSettings  # kept for legacy env loading during transition
except ImportError:
    # Fallback: direct execution (python scripts/nertz.py) — keeps developer UX simple
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import utils
    from parameters import Config, get_indicator
    from formulas import calculate_metrics, build_rich_metrics_snapshot
    from intelligence import IntelligenceLayer
    from settings import ConfigSettings

# ──────────────────────────────────────────────────────────────────────────────
# Event loop policy (Windows) - compatible con Pylance y runtime
# ──────────────────────────────────────────────────────────────────────────────
if sys.platform == "win32":
    policy_cls = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy_cls is not None:
        asyncio.set_event_loop_policy(policy_cls())


# Cargar configuración desde .env
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
config = ConfigSettings()

# Inyectar configuración en utils para eliminar hardcodeos
utils.set_config(config)

# Configuración de logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("NertzMetalEngine")

# URLs de Bybit basadas en el entorno
if config.BYBIT_ENV == "demo":
    BASE_URL = "https://api-demo.bybit.com"
    WS_URL = "wss://stream.bybit.com/v5/public/spot"  # Usar mainnet para datos públicos en demo
elif config.BYBIT_ENV == "live":
    BASE_URL = "https://api.bybit.com"
    WS_URL = "wss://stream.bybit.com/v5/public/spot"
else:
    BASE_URL = "https://api.bybit.com"  # Por defecto usar live
    WS_URL = "wss://stream.bybit.com/v5/public/spot"

def get_ntp_time() -> int:
    try:
        client = ntplib.NTPClient()
        response = client.request('pool.ntp.org')
        return int(response.tx_time * 1000)
    except Exception as e:
        logger.error(f"❌ Error al obtener tiempo NTP: {e}. Usando tiempo local.")
        return int(time.time() * 1000)

# DB models and engine moved to models.py (line reduction + cleaner structure)
from .models import (
    ActivePosition,
    Base,
    MarketData,
    Orderbook,
    MarketTicker,
    Trade,
    engine,
    SessionLocal,
)

# Crear tablas al importar (idempotente)
Base.metadata.create_all(bind=engine)


def get_db():
    """Generador de sesión para FastAPI Depends."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session():
    """Context manager para uso interno (with db_session() as db)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ──────────────────────────────────────────────────────────────────────────────
# Helper HTTP asíncrono (reemplaza llamadas a fetch_data indefinido)
# ──────────────────────────────────────────────────────────────────────────────
async def fetch_data(
    session: aiohttp.ClientSession,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Realiza GET HTTP usando aiohttp y retorna JSON o None en error.
    """
    if timeout is None:
        timeout = int(getattr(config, "HTTP_TIMEOUT_S", 10))
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    try:
        async with session.get(url, params=params, timeout=client_timeout) as resp:
            if resp.status == 200:
                return await resp.json()
            logger.warning(f"⚠️ HTTP {resp.status} en {url}")
            return None
    except Exception as e:
        logger.error(f"❌ Error fetch_data {url}: {e}")
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Helper para aplicar deltas de orderbook (Bybit v5 delta)
# ──────────────────────────────────────────────────────────────────────────────
def _update_orderbook(bid_dict: Dict[float, float], ask_dict: Dict[float, float], data: Dict) -> None:
    """
    Aplica actualizaciones delta al diccionario de bids/asks.
    data["data"] contiene "b" y "a" con listas [price, qty].
    """
    try:
        delta = data.get("data", {})
        for side_key, target in (("b", bid_dict), ("a", ask_dict)):
            for entry in delta.get(side_key, []):
                if len(entry) < 2:
                    continue
                price = float(entry[0])
                qty = float(entry[1])
                if qty <= 0:
                    target.pop(price, None)
                else:
                    target[price] = qty
    except Exception as e:
        logger.error(f"❌ Error aplicando delta de orderbook: {e}")


# Definir tipos específicos para la posición activa
ActivePositionData = Dict[str, Union[str, float, Any]]

class NertzMetalEngine:
    def __init__(self) -> None:
        self.symbols = config.SYMBOL.split(",")
        # Do not hardcode initial_capital from env here.
        # Use env only as fallback. Real initial will be captured from wallet balance at startup.
        self.capital = config.CAPITAL_USDT  # fallback for capital
        self.initial_capital = 0.0  # will be set to real wallet balance or loaded historical
        self.positions = {s: [] for s in self.symbols}
        self.active_position: Dict[str, Optional[ActivePositionData]] = {s: None for s in self.symbols}
        self.iterations = 0
        self.ws = None
        self.running = True
        self.orderbook_data = {s: {"bids": [], "asks": []} for s in self.symbols}
        self.ticker_data = {
            s: {
                "last_price": 0.0,
                "volume_24h": 0.0,
                "high_24h": 0.0,
                "low_24h": 0.0,
                "usd_index_price": 0.0,
            }
            for s in self.symbols
        }
        self.candles = {s: [] for s in self.symbols}
        self.trade_id_counter = 1
        self.last_orderbook_log = 0.0
        self.last_trade_time = {s: datetime.min.replace(tzinfo=timezone.utc) for s in self.symbols}
        self.last_ticker_trade_time = {s: 0.0 for s in self.symbols}
        self.last_kline_time = {s: 0 for s in self.symbols}
        self.last_balance_sync = 0.0

        self.trade_buffer = {s: [] for s in self.symbols}
        self.intelligence = IntelligenceLayer()  # Unified elegant AI layer (Qwen + XGB + Memory)
        self.last_predictions: Dict[str, Dict[str, Any]] = {}
        self.last_balance_details: Dict[str, Any] = {}
        self.bybit_session = None
        if config.BYBIT_API_KEY and config.BYBIT_API_SECRET:
            logger.info(f"Sesión Bybit HTTP reutilizable (env: {config.BYBIT_ENV})")
            self.bybit_session = HTTP(
                testnet=False, demo=(config.BYBIT_ENV == "demo"),
                api_key=config.BYBIT_API_KEY, api_secret=config.BYBIT_API_SECRET)

            # Capture real balance as early as possible for capital (before load for fresh)
            real_balance = self._fetch_real_balance()
            if real_balance > 0:
                self.capital = real_balance

        self._loaded_historical_initial = False
        self._load_results()
        self._load_active_positions()

        # If no historical initial loaded from previous results.json, capture the real starting capital from wallet.
        # This ensures capital_inicial in results reflects actual Bybit balance at the beginning of tracking,
        # not the env default. Historical value from load is preserved for continuity across restarts.
        if not self._loaded_historical_initial and self.initial_capital <= 0 and self.capital > 0:
            self.initial_capital = self.capital
            logger.info(f"📈 Capital inicial capturado del balance real de Bybit (inicio de sesión): {self.initial_capital:.2f} USDT")

    def _load_results(self) -> None:
        """Carga results.json si existe para restaurar historial de trades entre reinicios."""
        results_path = os.path.join(os.path.dirname(__file__), '..', 'logs', 'results.json')
        if not os.path.exists(results_path):
            return
        with open(results_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        saved_initial = data.get("metadata", {}).get("capital_inicial")
        if saved_initial and saved_initial > 0:
            self.initial_capital = float(saved_initial)
            self._loaded_historical_initial = True
        trades = data.get("trades", {})
        for sym in self.symbols:
            if sym in trades and isinstance(trades[sym], list):
                self.positions[sym] = trades[sym]
        max_id = 0
        for sym in self.symbols:
            for t in self.positions.get(sym, []):
                tid = t.get("trade_id", 0)
                if isinstance(tid, (int, float)) and tid > max_id:
                    max_id = int(tid)
        self.trade_id_counter = max_id + 1
        total = sum(len(v) for v in self.positions.values())
        if total > 0:
            logger.info(f"📂 Historial cargado: {total} trades, capital_inicial={self.initial_capital:.2f}, next_id={self.trade_id_counter}")

    def _load_active_positions(self) -> None:
        """Restore open positions from SQLite after restart (Hard Lock persistence)."""
        with SessionLocal() as db:
            for symbol in self.symbols:
                pos = db.query(ActivePosition).filter_by(symbol=symbol).first()
                if not pos:
                    self.active_position[symbol] = None
                    continue
                self.active_position[symbol] = {
                    "symbol": pos.symbol,
                    "timestamp": pos.timestamp.isoformat(),
                    "action": pos.action,
                    "entry_price": pos.entry_price,
                    "quantity": pos.quantity,
                    "tp": pos.tp,
                    "sl": pos.sl,
                    "order_id": pos.order_id,
                    "metrics": {
                        "combined": pos.combined,
                        "ild": pos.ild,
                        "egm": pos.egm,
                        "rol": pos.rol,
                        "pio": pos.pio,
                        "ogm": pos.ogm,
                    },
                }
                logger.info(
                    f"🔒 Posición activa restaurada para {symbol}: "
                    f"{pos.action.upper()} @ {pos.entry_price:.2f} TP={pos.tp:.2f} SL={pos.sl:.2f}"
                )

    # ── Balance real ─────────────────────────────────────────────

    def _fetch_real_balance(self) -> float:
        """Sync helper to get real USDT balance. Returns the value or 0."""
        if not self.bybit_session:
            return 0.0
        try:
            resp = self.bybit_session.get_wallet_balance(accountType="UNIFIED")
            data = resp[1] if isinstance(resp, tuple) else resp
            if isinstance(data, dict) and data.get("retCode") == 0:
                coins = data["result"]["list"][0].get("coin", [])
                usdt = next((float(c["walletBalance"]) for c in coins if c["coin"] == "USDT"), 0.0)
                return usdt
        except Exception as e:
            logger.warning(f"Could not fetch real balance: {e}")
        return 0.0

    async def _sync_balance(self) -> None:
        """Sincroniza self.capital con el balance USDT real de Bybit."""
        usdt = self._fetch_real_balance()
        if usdt > 0:
            old = self.capital
            self.capital = usdt
            self.last_balance_sync = time.time()
            self.last_balance_details = {
                "account_type": "UNIFIED",
                "coin": "USDT",
                "total_equity": usdt,
                "available_balance": usdt,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            # Record balance snapshot as event (like brother)
            utils.append_results_event({
                "type": "balance",
                "account_type": "UNIFIED",
                "coin": "USDT",
                "total_equity": usdt,
                "available_balance": usdt,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            logger.info(f"💰 Balance real sincronizado: {old:.2f} → {usdt:.2f} USDT")

    async def _maybe_sync_balance(self) -> None:
        if time.time() - self.last_balance_sync > config.BALANCE_SYNC_INTERVAL_S:
            await self._sync_balance()

    def _get_last_trade_timestamp(self) -> str | None:
        """Helper to get the most recent trade timestamp across symbols for metadata."""
        for sym in self.symbols:
            trades = self.positions.get(sym, [])
            if trades:
                return trades[-1].get("timestamp")
        return None

    def _record_metrics_snapshot(self, symbol: str, decision: str, cd: list, ob: dict, tk: dict) -> None:
        """Centralized, uses event append like brother project."""
        try:
            snap = build_rich_metrics_snapshot(
                candles=cd or [],
                orderbook=ob or {"bids": [], "asks": []},
                ticker=tk or {"last_price": 0},
                recent_trades=getattr(self, 'trade_buffer', {}).get(symbol, [])[-10:],
                symbol=symbol,
                thresholds={
                    "egm_buy_threshold": config.EGM_BUY_THRESHOLD,
                    "egm_sell_threshold": config.EGM_SELL_THRESHOLD,
                    "combined_buy_threshold": 1.0,
                    "combined_sell_threshold": -1.0,
                    "combined_hold_band": 0.0
                }
            )
            utils.append_results_event({"type": "metrics", "symbol": symbol, "last_price": tk.get("last_price", 0), "decision": decision, "metrics": snap, "thresholds": snap.get("thresholds", {}), "timestamp": datetime.now(timezone.utc).isoformat()})
        except Exception:
            pass

    async def fetch_initial_data(self) -> None:
        async with aiohttp.ClientSession() as session:
            tasks = [self._fetch_symbol_data(session, symbol) for symbol in self.symbols]
            await asyncio.gather(*tasks, return_exceptions=True)
    async def _fetch_symbol_data(self, session: aiohttp.ClientSession, symbol: str) -> None:
        kline_url = f"{BASE_URL}/v5/market/kline"
        params = {"category": "spot", "symbol": symbol, "interval": config.TIMEFRAME.replace("m", ""), "limit": "50"}
        kline_response = await fetch_data(session, kline_url, params)
        if kline_response and "result" in kline_response and "list" in kline_response["result"]:
            with db_session() as db:
                candles = [
                    MarketData(
                        timestamp=utils.timestamp_to_datetime(int(k[0])),
                        symbol=symbol,
                        open=float(k[1]),
                        high=float(k[2]),
                        low=float(k[3]),
                        close=float(k[4]),
                        volume=float(k[5])
                    ) for k in kline_response["result"]["list"]
                ]
                for candle in candles:
                    if not db.query(MarketData).filter_by(timestamp=candle.timestamp, symbol=symbol).first():
                        db.add(candle)
                db.commit()

                self.candles[symbol] = [
                    {
                        "timestamp": c.timestamp.isoformat(),
                        "symbol": c.symbol,
                        "open": c.open,
                        "high": c.high,
                        "low": c.low,
                        "close": c.close,
                        "volume": c.volume
                    } for c in candles
                ]
                self._maintain_recent_candles(symbol)
            logger.info(f"📈 Velas iniciales para {symbol}: {len(self.candles[symbol])}")

        orderbook_url = f"{BASE_URL}/v5/market/orderbook"
        params = {"category": "spot", "symbol": symbol, "limit": "100"}
        orderbook_response = await fetch_data(session, orderbook_url, params)
        if orderbook_response and "result" in orderbook_response:
            self.orderbook_data[symbol] = {"bids": orderbook_response["result"]["b"], "asks": orderbook_response["result"]["a"]}
            logger.info(f"📊 Orderbook inicial para {symbol}: Bids={len(self.orderbook_data[symbol]['bids'])}, Asks={len(self.orderbook_data[symbol]['asks'])}")

        ticker_url = f"{BASE_URL}/v5/market/tickers"
        params = {"category": "spot", "symbol": symbol}
        ticker_response = await fetch_data(session, ticker_url, params)
        if ticker_response and "result" in ticker_response and "list" in ticker_response["result"]:
            ticker_data = ticker_response["result"]["list"][0]
            self.ticker_data[symbol] = {
                "last_price": float(ticker_data["lastPrice"]),
                "volume_24h": float(ticker_data["volume24h"]),
                "high_24h": float(ticker_data["highPrice24h"]),
                "low_24h": float(ticker_data["lowPrice24h"])
            }
            logger.info(f"⚡ Ticker inicial para {symbol}: {self.ticker_data[symbol]['last_price']}")

    def _maintain_recent_candles(self, symbol: str, max_len: int = 50) -> None:
        """Ensure self.candles[symbol] is sorted oldest->newest and trimmed. Critical for correct [-N:] in metrics/XGB."""
        if symbol not in self.candles:
            self.candles[symbol] = []
            return
        # Sort by timestamp string (iso sortable) or numeric if present
        def _ts_key(c):
            ts = c.get("timestamp", "")
            if isinstance(ts, (int, float)):
                return ts
            return str(ts)
        self.candles[symbol].sort(key=_ts_key)
        # Dedup by timestamp
        seen = {}
        for c in self.candles[symbol]:
            seen[c.get("timestamp")] = c
        self.candles[symbol] = list(seen.values())[-max_len:]

    async def start_async(self) -> None:
        logger.info(f"🔥 Iniciando bot para {self.symbols}")
        await self._sync_balance()  # balance real al arrancar

        # Safety capture after full first sync (in case early fetch in init didn't get real balance)
        if not getattr(self, '_loaded_historical_initial', False) and self.initial_capital <= 0:
            if self.capital > 0:
                self.initial_capital = self.capital
                logger.info(f"📈 Capital inicial establecido desde balance real (post-sync): {self.initial_capital:.2f} USDT")
            else:
                self.initial_capital = config.CAPITAL_USDT
                logger.warning(f"Fallback a capital inicial desde env: {self.initial_capital}")

        for attempt in range(config.WS_MAX_RECONNECT_ATTEMPTS):
            if not self.running:
                break
            try:
                logger.info(f"Intento {attempt + 1}/{config.WS_MAX_RECONNECT_ATTEMPTS} para obtener datos iniciales...")
                await self.fetch_initial_data()
                await self._connect_websocket_async()
                break
            except Exception as e:
                logger.error(f"❌ Error al iniciar (intento {attempt + 1}): {e}")
                if attempt < config.WS_MAX_RECONNECT_ATTEMPTS - 1:
                    await asyncio.sleep(config.WS_RECONNECT_DELAY_S)
                else:
                    logger.critical("❌ Máximo de intentos alcanzado. Deteniendo.")
                    self.stop()

    async def _connect_websocket_async(self) -> None:
        reconnect_delay = config.WS_RECONNECT_DELAY_S
        max_attempts = config.WS_MAX_RECONNECT_ATTEMPTS
        attempt = 0
        while self.running and (max_attempts <= 0 or attempt < max_attempts):
            try:
                async with websockets.connect(WS_URL) as ws:
                    self.ws = ws
                    attempt = 0  # reset on success
                    logger.info("🌐 WebSocket abierto")
                    await self._resubscribe_async()
                    async for message in ws:
                        await self._on_message(ws, message)
            except websockets.ConnectionClosed as e:
                attempt += 1
                logger.warning(f"⚠️ WebSocket cerrado: {e}, intentando reconectar en {reconnect_delay}s (intento {attempt})")
                await asyncio.sleep(reconnect_delay)
            except Exception as e:
                attempt += 1
                logger.error(f"❌ Error en WebSocket: {e}")
                await asyncio.sleep(reconnect_delay)
        if attempt >= max_attempts > 0:
            logger.critical("❌ Máximo de intentos de reconexión WS alcanzado. Deteniendo.")
            self.stop()

    async def _resubscribe_async(self) -> None:
        interval = config.TIMEFRAME.replace("m", "")
        for symbol in self.symbols:
            subscription = {"op": "subscribe", "args": [f"kline.{interval}.{symbol}", f"orderbook.50.{symbol}", f"tickers.{symbol}"]}
            if self.ws:
                await self.ws.send(json.dumps(subscription))
                logger.info(f"📡 Suscrito a {symbol}")

    async def _on_message(self, ws: Any, message: Any) -> None:
        with db_session() as db:
            if isinstance(message, bytes):
                message = message.decode('utf-8')
            elif isinstance(message, tuple):
                message = message[0]
            elif message is None:
                logger.warning("⚠️ Mensaje recibido es None, ignorando.")
                return

            if isinstance(message, str):
                data = json.loads(message)
                logger.debug(f"📨 Mensaje procesado: {json.dumps(data, indent=2)}")

                if "topic" not in data:
                    logger.debug("⚠️ Mensaje sin tema ('topic'), posiblemente ping/pong.")
                    if data.get("op") == "ping" and ws is not None:
                        await ws.send(json.dumps({"op": "pong", "ts": data.get("ts", int(time.time() * 1000))}))
                    return

                symbol = data["topic"].split(".")[-1]
                if symbol not in self.symbols:
                    logger.warning(f"⚠️ Símbolo desconocido: {symbol}")
                    return

                if "kline" in data["topic"] and data.get("data") and len(data["data"]) > 0:
                    await self._handle_kline(symbol, data["data"][0], db)
                elif "orderbook" in data["topic"] and data.get("data"):
                    await self._handle_orderbook(symbol, data, db)
                elif "tickers" in data["topic"] and data.get("data"):
                    await self._handle_ticker(symbol, data["data"], db)
                    await self._execute_trade_on_ticker(symbol, db)

    async def _handle_kline(self, symbol: str, kline: Dict, db: Session) -> None:
        timestamp_value = kline.get("start")
        if not timestamp_value or not str(timestamp_value).isdigit():
            logger.warning(f"⚠️ Timestamp inválido '{timestamp_value}' para {symbol}. Saltando.")
            return
        timestamp = utils.timestamp_to_datetime(int(timestamp_value))

        volume = float(kline.get("volume", 0))
        open_price = float(kline.get("open", 0))
        high_price = float(kline.get("high", 0))
        low_price = float(kline.get("low", 0))
        close_price = float(kline.get("close", 0))

        logger.debug(f"📥 Kline recibido para {symbol}: timestamp={timestamp}, close={close_price}, volume={volume}")

        if not db.query(MarketData).filter_by(timestamp=timestamp, symbol=symbol).first():
            candle = MarketData(
                timestamp=timestamp, symbol=symbol, open=open_price, high=high_price,
                low=low_price, close=close_price, volume=volume
            )
            db.add(candle)
            db.commit()

            self.candles[symbol].append({
                "timestamp": candle.timestamp.isoformat(),
                "symbol": candle.symbol,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume
            })
            self._maintain_recent_candles(symbol)
            self.last_kline_time[symbol] = int(timestamp_value)
            logger.info(f"⚡ Kline para {symbol}: Close={candle.close}, Volume={candle.volume}")
            logger.info(f"📈 Acumulados {len(self.candles[symbol])} velas para {symbol}")

            await self._execute_trade(symbol, db)

    async def _handle_orderbook(self, symbol: str, data: Dict, db: Session) -> None:
        if data.get("type") == "snapshot":
            self.orderbook_data[symbol] = {"bids": data["data"]["b"], "asks": data["data"]["a"]}
            await self._store_orderbook(symbol, db)
            logger.info(f"📊 Snapshot para {symbol}: Bids={len(self.orderbook_data[symbol]['bids'])}, Asks={len(self.orderbook_data[symbol]['asks'])}")
        elif data.get("type") == "delta":
            if symbol not in self.orderbook_data or not self.orderbook_data[symbol]["bids"]:
                logger.warning(f"⚠️ No hay orderbook previo para {symbol}, esperando snapshot")
                return
            current = self.orderbook_data[symbol]
            bid_dict = {float(b[0]): float(b[1]) for b in current["bids"]}
            ask_dict = {float(a[0]): float(a[1]) for a in current["asks"]}
            _update_orderbook(bid_dict, ask_dict, data)
            self.orderbook_data[symbol] = {
                "bids": [[str(p), str(q)] for p, q in sorted(bid_dict.items(), reverse=True) if q > 0][:50],
                "asks": [[str(p), str(q)] for p, q in sorted(ask_dict.items()) if q > 0][:50]
            }
            await self._store_orderbook(symbol, db)
            logger.info(f"📊 Delta para {symbol}: Bids={len(self.orderbook_data[symbol]['bids'])}, Asks={len(self.orderbook_data[symbol]['asks'])}")

    async def _store_orderbook(self, symbol: str, db: Session) -> None:
        orderbook = Orderbook(
            timestamp=datetime.now(timezone.utc), symbol=symbol,
            bids=json.dumps(self.orderbook_data[symbol]["bids"]),
            asks=json.dumps(self.orderbook_data[symbol]["asks"])
        )
        db.add(orderbook)
        db.commit()
        if time.time() - self.last_orderbook_log >= config.ORDERBOOK_LOG_INTERVAL_S:
            logger.info(f"🤘 Orderbook guardado para {symbol}: Bids={len(self.orderbook_data[symbol]['bids'])}, Asks={len(self.orderbook_data[symbol]['asks'])}")
            self.last_orderbook_log = time.time()

    async def _handle_ticker(self, symbol: str, ticker_data: Any, db: Session) -> None:
        # Normalize: stream often sends list, take first item
        if isinstance(ticker_data, list) and ticker_data:
            ticker = ticker_data[0]
        else:
            ticker = ticker_data

        if not isinstance(ticker, dict) or not ticker:
            logger.warning(f"⚠️ Ticker inválido para {symbol}: {ticker}. Saltando.")
            return

        required = ["lastPrice", "volume24h", "highPrice24h", "lowPrice24h"]
        optional = ["usdIndexPrice"]
        if not all(key in ticker for key in required):
            logger.warning(f"⚠️ Faltan claves requeridas en ticker para {symbol}: {ticker}. Saltando.")
            return

        ticker_values = {}
        for key in required + optional:
            value = ticker.get(key, 0.0)
            ticker_values[key] = float(value) if value else 0.0

        if ticker_values.get("usdIndexPrice", 0.0) <= 0:
            ticker_values["usdIndexPrice"] = self.ticker_data.get(symbol, {}).get("usd_index_price", 0.0)

        self.ticker_data[symbol] = {
            "last_price": ticker_values["lastPrice"],
            "volume_24h": ticker_values["volume24h"],
            "high_24h": ticker_values["highPrice24h"],
            "low_24h": ticker_values["lowPrice24h"],
            "usd_index_price": ticker_values["usdIndexPrice"]
        }

        market_ticker = MarketTicker(
            timestamp=datetime.now(timezone.utc),
            symbol=symbol,
            last_price=self.ticker_data[symbol]["last_price"],
            volume_24h=self.ticker_data[symbol]["volume_24h"],
            high_24h=self.ticker_data[symbol]["high_24h"],
            low_24h=self.ticker_data[symbol]["low_24h"]
        )
        db.add(market_ticker)
        db.commit()

        logger.info(f"⚡ Ticker actualizado para {symbol}: Last={self.ticker_data[symbol]['last_price']}, USDIndex={self.ticker_data[symbol]['usd_index_price']}")

        # Periodic rich metrics snapshot (throttled)
        now_ts = time.time()
        last_ts = getattr(self, 'last_snapshot_ts', {}).get(symbol, 0)
        if len(self.candles.get(symbol, [])) >= 5 and (now_ts - last_ts) > 30:  # every ~30s
            if not hasattr(self, 'last_snapshot_ts'):
                self.last_snapshot_ts = {}
            self.last_snapshot_ts[symbol] = now_ts
            cdata = [{"open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"], "volume": c["volume"]} for c in self.candles[symbol][-20:]]
            obdata = self.orderbook_data.get(symbol, {"bids": [], "asks": []})
            tkdata = self.ticker_data[symbol]
            self._record_metrics_snapshot(symbol, "monitoring", cdata, obdata, tkdata)

        await self._check_active_positions(symbol, self.ticker_data[symbol]['last_price'], db)

    async def _determine_decision(self, symbol: str, metrics: Dict[str, float]) -> str:
        egm = metrics.get("egm", 0.0)
        combined = metrics.get("combined", 0.0)

        local_decision = "hold"

        # Prefer TSM pipeline outputs (QuestDB/Postgres-validated formulas)
        best_combined = (
            metrics.get("spot_pressure_fusion")
            or metrics.get("combined_tsm")
            or metrics.get("combined_tsm_inspired")
            or combined
        )

        if egm >= Config.THRESHOLDS.egm_buy or best_combined >= Config.THRESHOLDS.combined_buy:
            local_decision = "buy"
        elif egm <= Config.THRESHOLDS.egm_sell or best_combined <= Config.THRESHOLDS.combined_sell:
            local_decision = "sell"

        if local_decision == "hold":
            return "hold"

        # === Elegant Intelligence Layer (XGB + Memory + Qwen) ===
        candles = self.candles.get(symbol, [])
        recent_trades = self.positions.get(symbol, [])

        intel = await self.intelligence.evaluate(
            symbol=symbol,
            local_action=local_decision,
            metrics=metrics,
            candles=candles,
            recent_trades=recent_trades,
        )

        xgb = intel["xgb"]
        qwen = intel["qwen"]

        logger.info(
            f"🧠 {symbol} | Local={local_decision} | "
            f"XGB={xgb['action']}({xgb['prob_up']:.2f}) | "
            f"Qwen={qwen.get('action')} conf={qwen.get('confidence', 0):.2f}"
        )

        final = qwen.get("action", "hold").lower()

        cdata = [{"open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"], "volume": c["volume"]} for c in candles[-20:]] if candles else []
        obdata = self.orderbook_data.get(symbol, {"bids": [], "asks": []})
        tkdata = self.ticker_data.get(symbol, {"last_price": 0})
        self._record_metrics_snapshot(symbol, final if final != local_decision else local_decision, cdata, obdata, tkdata)

        if final == local_decision:
            logger.info(f"✅ DECISION CONFIRMED: {local_decision.upper()} — {qwen.get('reason', '')}")
            return local_decision

        logger.info(f"🛑 DECISION OVERRULED → HOLD (Qwen suggested {final})")
        return "hold"

    def _get_extra_bybit_analysis(self, symbol: str) -> Optional[str]:
        """Fetch richer market context using existing Bybit session (or MCP tools when available)."""
        if not self.bybit_session:
            return None
        try:
            # Funding (linear)
            fr = self.bybit_session.get_funding_rate(category="linear", symbol=symbol, limit=1)
            funding = "N/A"
            if isinstance(fr, (list, tuple)) and len(fr) > 1 and fr[1].get("retCode") == 0:
                lst = fr[1].get("result", {}).get("list", [])
                if lst:
                    funding = f"{float(lst[0].get('fundingRate', 0))*100:.4f}%"

            # Simple ticker supplement
            tk = self.ticker_data.get(symbol, {})
            return f"Funding: {funding} | 24h Vol: {tk.get('volume_24h', 0):.0f}"
        except Exception:
            return None

    async def _execute_trade(self, symbol: str, db: Session) -> None:
        if self.active_position.get(symbol) is not None:
            return

        await self._maybe_sync_balance()

        candles = self.candles.get(symbol, [])
        cd = [{"open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"], "volume": c["volume"]} for c in candles]
        ob = self.orderbook_data.get(symbol, {"bids": [], "asks": []})
        tk = self.ticker_data.get(symbol, {"last_price": 0.0})

        # Use the new high-quality formulas module
        rich_metrics = calculate_metrics(
            cd, ob, tk,
            return_variations=True,
            symbol=symbol,
            recent_trades=getattr(self, "trade_buffer", {}).get(symbol, [])[-20:],
        )
        logger.info(f"📊 {symbol} metrics: { {k: round(v,4) for k,v in rich_metrics.items() if isinstance(v, (int, float)) and not k.startswith('var')} }")

        self._record_metrics_snapshot(symbol, "evaluating", cd, ob, tk)

        if len(candles) < 5:
            return
        now = datetime.now(timezone.utc)
        if now <= self.last_trade_time.get(symbol, datetime.min.replace(tzinfo=timezone.utc)) + timedelta(seconds=config.DEFAULT_SLEEP_TIME):
            return

        metrics = rich_metrics
        decision = await self._determine_decision(symbol, metrics)
        if decision == "hold":
            return

        last_price = tk.get("last_price", 0.0)
        if last_price <= 0:
            return

        vol = max(metrics.get("volatility", config.BASE_VOLATILITY), config.MIN_VOLATILITY_FLOOR)
        risk = self.capital * config.RISK_FACTOR
        qty = (risk / last_price) * (config.BASE_VOLATILITY / vol)
        qty = max(config.MIN_TRADE_SIZE, min(qty, config.MAX_TRADE_SIZE, (self.capital * config.MAX_POSITION_PCT) / last_price))

        if qty * last_price > self.capital:
            qty = (self.capital * config.MAX_POSITION_PCT) / last_price
            if qty < config.MIN_TRADE_SIZE:
                return

        if config.BYBIT_API_KEY and config.BYBIT_API_SECRET:
            if not await self._check_sufficient_balance(symbol, decision, qty):
                return

        # Use simple dynamic TP/SL from formulas (no extra class)
        tp, sl = utils.calculate_tp_sl(last_price, vol, decision) if hasattr(utils, 'calculate_tp_sl') else (last_price * (1 + 0.02 if decision == "buy" else 1 - 0.02), last_price * (1 - 0.01 if decision == "buy" else 1 + 0.01))
        # fallback simple
        if tp == 0 and sl == 0:
            tp = last_price * (1.02 if decision == "buy" else 0.98)
            sl = last_price * (0.99 if decision == "buy" else 1.01)

        result = await self._place_order(symbol, decision, qty, last_price, tp, sl)
        if not result.get("success"):
            logger.error(f"❌ Fallo al colocar orden para {symbol}: {result.get('message')}")
            return

        # Capture rich decision snapshot (like professional results)
        rich_metrics = calculate_metrics(
            cd, ob, tk,
            return_variations=True,
            symbol=symbol,
            recent_trades=getattr(self, "trade_buffer", {}).get(symbol, [])[-20:],
        )
        decision_snapshot = {
            "type": "metrics",
            "symbol": symbol,
            "last_price": last_price,
            "decision": decision,
            "metrics": rich_metrics,
            "thresholds": {
                "egm_buy_threshold": config.EGM_BUY_THRESHOLD,
                "egm_sell_threshold": config.EGM_SELL_THRESHOLD,
                "combined_buy_threshold": 1.0,
                "combined_sell_threshold": -1.0,
                "combined_hold_band": 0.0
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        utils.append_results_event(decision_snapshot)

        active_pos = ActivePosition(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            action=decision,
            entry_price=last_price,
            quantity=qty,
            tp=tp,
            sl=sl,
            combined=float(metrics.get("combined", 0.0)),
            ild=float(metrics.get("ild", 0.0)),
            egm=float(metrics.get("egm", 0.0)),
            rol=float(metrics.get("rol", 0.0)),
            pio=float(metrics.get("pio", 0.0)),
            ogm=float(metrics.get("ogm", 0.0)),
            order_id=str(result.get("order_id") or ""),
        )
        db.add(active_pos)
        db.commit()

        self.active_position[symbol] = {
            "symbol": symbol,
            "timestamp": active_pos.timestamp.isoformat(),
            "action": decision,
            "entry_price": last_price,
            "quantity": qty,
            "tp": tp,
            "sl": sl,
            "order_id": result.get("order_id"),
            "metrics": {
                "combined": metrics.get("combined", 0),
                "ild": metrics.get("ild", 0),
                "egm": metrics.get("egm", 0),
                "rol": metrics.get("rol", 0),
                "pio": metrics.get("pio", 0),
                "ogm": metrics.get("ogm", 0),
            },
            "bybit_raw": result.get("bybit_raw", {}),
        }

        logger.info(f"🔒 {symbol}: {decision.upper()} @ {last_price:.2f} TP={tp:.2f} SL={sl:.2f} Qty={qty:.6f}")

        self.iterations += 1
        if config.MAX_ITERATIONS > 0 and self.iterations >= config.MAX_ITERATIONS:
            logger.info("🏁 Máximo de iteraciones alcanzado. Deteniendo bot.")
            self.stop()

    async def _check_active_positions(self, symbol: str, current_price: float, db: Session) -> None:
        pos = self.active_position.get(symbol)
        if not pos: return
        action = str(pos["action"])
        tp = float(pos["tp"])
        sl = float(pos["sl"])
        entry = float(pos["entry_price"])
        qty = float(pos["quantity"])
        hit_tp = (action=="buy" and current_price>=tp) or (action=="sell" and current_price<=tp)
        hit_sl = (action=="buy" and current_price<=sl) or (action=="sell" and current_price>=sl)
        if not (hit_tp or hit_sl): return
        reason = "TP hit" if hit_tp else "SL hit"
        exit_p = current_price
        exit_a = "sell" if action == "buy" else "buy"
        logger.info(f"⚡ {symbol} {reason}")
        order_res = await self._place_order(symbol, exit_a, qty, exit_p, 0, 0)
        if not order_res.get("success"):
            return
        pos.setdefault("bybit_raw", {})["exit"] = order_res.get("bybit_raw", order_res)
        g = (exit_p - entry) * qty if action == "buy" else (entry - exit_p) * qty
        fee = (entry + exit_p) * qty * config.FEE_RATE
        net = g - fee
        self.capital = self.capital + net if 0 < self.capital + net < 1e15 else self.initial_capital
        self.trade_id_counter += 1
        m = pos.get("metrics", {}) or {}
        rr = self._compute_risk_reward(action, entry, tp, sl)
        td = {"trade_id": self.trade_id_counter-1, "timestamp": datetime.now(timezone.utc).isoformat(), "symbol": symbol, "action": action, "order_id": pos.get("order_id"), "entry_price": entry, "exit_price": exit_p, "tp_price": tp, "sl_price": sl, "quantity": qty, "profit_loss": round(net, 8), "outcome_status": "final", "outcome_timestamp": datetime.now(timezone.utc).isoformat(), "bybit_raw": pos.get("bybit_raw", {}), "decision": action, "combined": m.get("combined", 0), "ild": m.get("ild", 0), "egm": m.get("egm", 0), "rol": m.get("rol", 0), "pio": m.get("pio", 0), "ogm": m.get("ogm", 0), "risk_reward_ratio": rr, "metrics_at_entry": m}
        self._record_closed_trade(symbol, td, pos, m, rr, db)
        await self._save_results()
        logger.info(f"💰 {symbol} CLOSED {reason} PnL={net:.2f}")

    # --- Clean supporting methods (small, focused, override-friendly) ---

    def _compute_risk_reward(self, action: str, entry: float, tp: Any, sl: Any) -> float:
        """Compute risk/reward ratio cleanly."""
        try:
            tp = float(tp)
            sl = float(sl)
            if action == "buy" and sl != entry:
                return abs(tp - entry) / abs(entry - sl)
            if action == "sell" and sl != entry:
                return abs(entry - tp) / abs(sl - entry)
        except Exception:
            pass
        return 0.0

    def _record_closed_trade(
        self,
        symbol: str,
        trade_dict: Dict[str, Any],
        pos: Dict[str, Any],
        metrics: Dict[str, Any],
        risk_reward: float,
        db: Session,
    ) -> None:
        """Append to engine state, persist to DB (if available), update timing."""
        self.positions.setdefault(symbol, []).append(trade_dict)
        self.trade_buffer[symbol].append(trade_dict)
        self.last_trade_time[symbol] = datetime.now(timezone.utc)
        self.active_position[symbol] = None
        try:
            db.query(ActivePosition).filter_by(symbol=symbol).delete()
            db.commit()
        except Exception as exc:
            logger.warning(f"Active position cleanup skipped for {symbol}: {exc}")
            try:
                db.rollback()
            except Exception:
                pass

        # Persist to SQLite trades table (bot-generated data only)
        try:
            row = Trade(
                trade_id=trade_dict["trade_id"],
                timestamp=datetime.now(timezone.utc),
                symbol=symbol,
                action=trade_dict["action"],
                entry_price=float(trade_dict["entry_price"]),
                exit_price=float(trade_dict["exit_price"]),
                quantity=float(trade_dict["quantity"]),
                profit_loss=float(trade_dict["profit_loss"]),
                decision=trade_dict["action"],
                combined=float(metrics.get("combined", 0.0)),
                ild=float(metrics.get("ild", 0.0)),
                egm=float(metrics.get("egm", 0.0)),
                rol=float(metrics.get("rol", 0.0)),
                pio=float(metrics.get("pio", 0.0)),
                ogm=float(metrics.get("ogm", 0.0)),
                risk_reward_ratio=round(risk_reward, 4),
            )
            db.add(row)
            db.commit()
        except Exception as exc:
            logger.warning(f"DB trade persist skipped for {symbol}: {exc}")
            try:
                db.rollback()
            except Exception:
                pass

    async def _execute_trade_on_ticker(self, symbol: str, db: Session) -> None:
        now = time.time()
        if (now * 1000 - self.last_kline_time.get(symbol, 0) > config.KLINE_STALE_MS
                and now - self.last_ticker_trade_time.get(symbol, 0) > config.TICKER_TRADE_COOLDOWN_S):
            self.last_ticker_trade_time[symbol] = now
            await self._execute_trade(symbol, db)

    async def _check_sufficient_balance(self, symbol: str, action: str, quantity: float) -> bool:
        if not self.bybit_session:
            return True
        try:
            resp = self.bybit_session.get_wallet_balance(accountType="UNIFIED")
            data = resp[1] if isinstance(resp, tuple) else resp
            if not (isinstance(data, dict) and data.get("retCode") == 0):
                return True
            coins = data["result"]["list"][0].get("coin", [])
            if action.lower() == "buy":
                bal = next((float(c.get("walletBalance", 0)) for c in coins if c.get("coin") == "USDT"), 0)
                return bal >= quantity * self.ticker_data.get(symbol, {}).get("last_price", 0)
            base = symbol.replace("USDT", "")
            bal = next((float(c.get("walletBalance", 0)) for c in coins if c.get("coin") == base), 0)
            return bal >= quantity
        except Exception:
            return True

    async def _place_order(self, symbol: str, action: str, quantity: float, price: float, tp: float, sl: float) -> Dict[str, Any]:
        if not symbol or action.lower() not in ("buy", "sell") or quantity <= 0:
            return {"success": False, "order_id": None, "message": "bad params"}
        if price <= 0 and config.ORDER_TYPE == "limit":
            return {"success": False, "order_id": None, "message": "bad price"}

        for attempt in range(config.ORDER_MAX_RETRIES):
            try:
                if not config.BYBIT_API_KEY or not config.BYBIT_API_SECRET:
                    return {"success": True, "order_id": f"sim-{self.trade_id_counter}", "message": "sim"}
                if not self.bybit_session:
                    self.bybit_session = HTTP(testnet=False, demo=(config.BYBIT_ENV == "demo"), api_key=config.BYBIT_API_KEY, api_secret=config.BYBIT_API_SECRET)
                params = {"category": "spot", "symbol": symbol, "side": "Buy" if action == "buy" else "Sell", "orderType": config.ORDER_TYPE, "qty": f"{quantity:.6f}", "timeInForce": config.TIME_IN_FORCE}
                if config.ORDER_TYPE == "limit":
                    params["price"] = f"{price:.2f}"
                response = self.bybit_session.place_order(**params)
                resp_data = response[1] if isinstance(response, tuple) else response
                if isinstance(resp_data, dict) and resp_data.get("retCode") == 0:
                    oid = resp_data["result"]["orderId"]
                    logger.info(f"✅ {symbol} {action.upper()} {quantity:.6f} @ {price:.2f} OrderID={oid}")
                    return {"success": True, "order_id": oid, "message": "OK", "bybit_raw": {"http_status": 200, "retCode": 0, "result": resp_data.get("result", {})}}
                raise Exception(resp_data.get("retMsg", "err") if isinstance(resp_data, dict) else "err")
            except Exception as e:
                if attempt < config.ORDER_MAX_RETRIES - 1:
                    await asyncio.sleep(min(config.ORDER_RETRY_BASE_DELAY_S ** attempt, config.ORDER_MAX_BACKOFF_S))
                else:
                    return {"success": False, "order_id": None, "message": str(e)}
        return {"success": False, "order_id": None, "message": "max retries"}

    def reset_trades(self) -> None:
        self.positions = {symbol: [] for symbol in self.symbols}
        self.active_position = {symbol: None for symbol in self.symbols}
        self.trade_id_counter = 1
        # For reset: capture current real wallet as new initial (don't hardcode env)
        if self.bybit_session:
            # sync to get fresh
            try:
                resp = self.bybit_session.get_wallet_balance(accountType="UNIFIED")
                data = resp[1] if isinstance(resp, tuple) else resp
                if isinstance(data, dict) and data.get("retCode") == 0:
                    coins = data["result"]["list"][0].get("coin", [])
                    usdt = next((float(c["walletBalance"]) for c in coins if c["coin"] == "USDT"), 0.0)
                    if usdt > 0:
                        self.initial_capital = usdt
                        self.capital = usdt
            except Exception:
                pass
        if self.initial_capital <= 0:
            self.initial_capital = config.CAPITAL_USDT  # absolute last resort
        # Limpiar results.json
        results_path = os.path.join(os.path.dirname(__file__), '..', 'logs', 'results.json')
        if os.path.exists(results_path):
            os.remove(results_path)
        with SessionLocal() as db:
            db.query(Trade).delete()
            db.query(ActivePosition).delete()
            db.commit()
        logger.info("🧹 Trades, posiciones activas y results.json reseteados")

    async def _save_results(self) -> None:
        total_profit = sum(trade["profit_loss"] for sym in self.symbols for trade in self.positions.get(sym, []) if trade["profit_loss"] > 0)
        total_loss = sum(trade["profit_loss"] for sym in self.symbols for trade in self.positions.get(sym, []) if trade["profit_loss"] < 0)
        total_trades = sum(len(self.positions.get(sym, [])) for sym in self.symbols)

        net_profit = total_profit + total_loss
        win_rate = (len([trade for sym in self.symbols for trade in self.positions.get(sym, []) if trade["profit_loss"] > 0]) / total_trades) if total_trades > 0 else 0
        avg_profit_per_trade = net_profit / total_trades if total_trades > 0 else 0

        log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')

        # Use event append like sibling project for rich, append-only professional results
        summary_event = {
            "type": "summary",
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "capital_inicial": self.initial_capital,
                "capital_actual": self.capital,
                "capital_final": self.capital,
                "capital_source": "bybit_wallet_balance",
                "capital_pnl": round(self.capital - self.initial_capital, 6),
                "total_pnl": round(net_profit, 6),
                "total_trades": total_trades,
                "iterations": self.iterations,
                "running": self.running,
                "balance_timestamp": self.last_balance_details.get("timestamp", datetime.now(timezone.utc).isoformat()),
                "balance_total_equity": self.last_balance_details.get("total_equity", self.capital),
                "balance_available_balance": self.last_balance_details.get("available_balance", self.capital),
                "balance_account_type": self.last_balance_details.get("account_type", "UNIFIED"),
                "balance_coin": self.last_balance_details.get("coin", "USDT"),
                "last_trade_timestamp": self._get_last_trade_timestamp()
            },
            "summary": {
                "total_profit": round(total_profit, 6),
                "total_loss": round(total_loss, 6),
                "net_profit": round(net_profit, 6),
                "win_rate": round(win_rate * 100, 2),
                "avg_profit_per_trade": round(avg_profit_per_trade, 6)
            },
            "by_symbol": {
                sym: {
                    "profit": round(sum(t["profit_loss"] for t in self.positions.get(sym, []) if t["profit_loss"] > 0), 2),
                    "loss": round(sum(t["profit_loss"] for t in self.positions.get(sym, []) if t["profit_loss"] < 0), 2),
                    "net_profit": round(sum(t["profit_loss"] for t in self.positions.get(sym, [])), 6),
                    "trade_count": len(self.positions.get(sym, []))
                } for sym in self.symbols
            },
            "trades": {sym: self.positions[sym] for sym in self.symbols},
        }
        utils.append_results_event(summary_event, log_dir)

        logger.info(f"📊 Resultados (event append style): PNL={round(net_profit, 2)} Capital={self.capital:.2f}")

        self.trade_buffer = {sym: [] for sym in self.symbols}

    def stop(self) -> None:
        self.running = False
        if self.ws:
            asyncio.create_task(self.ws.close())
        asyncio.create_task(self._save_results())
        logger.info("🛑 Bot detenido.")

app = FastAPI()
bot = NertzMetalEngine()

# ──────────────────────────────────────────────────────────────────────────────
# Helpers para endpoints (evitan repetición de getattr/float y queries)
# ──────────────────────────────────────────────────────────────────────────────
def _safe_float(val: Any, default: float = 0.0) -> float:
    """Conversión segura a float para datos de DB o dicts."""
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _candle_row_to_dict(c: MarketData) -> Dict[str, float]:
    """Convierte fila de MarketData a dict normalizado para métricas."""
    return {
        "open": _safe_float(getattr(c, "open", 0.0)),
        "high": _safe_float(getattr(c, "high", 0.0)),
        "low": _safe_float(getattr(c, "low", 0.0)),
        "close": _safe_float(getattr(c, "close", 0.0)),
        "volume": _safe_float(getattr(c, "volume", 0.0)),
    }


def _ticker_row_to_dict(t: Optional[MarketTicker]) -> Dict[str, float]:
    """Convierte fila de MarketTicker a dict normalizado."""
    if not t:
        return {"last_price": 0.0, "volume_24h": 0.0, "high_24h": 0.0, "low_24h": 0.0}
    return {
        "last_price": _safe_float(getattr(t, "last_price", 0.0)),
        "volume_24h": _safe_float(getattr(t, "volume_24h", 0.0)),
        "high_24h": _safe_float(getattr(t, "high_24h", 0.0)),
        "low_24h": _safe_float(getattr(t, "low_24h", 0.0)),
    }


def _safe_timestamp(ts: Any) -> str:
    """Safely convert any timestamp (datetime, str, or other) to ISO format string.
    Prevents AttributeError on .isoformat() if column returns string from DB."""
    if hasattr(ts, "isoformat"):
        return ts.isoformat()
    if isinstance(ts, str):
        return ts
    return datetime.now(timezone.utc).isoformat()


def _get_live_metrics(symbol: str) -> Dict[str, float]:
    """Calcula métricas usando datos en memoria del bot (rápido, sin DB)."""
    candles = bot.candles.get(symbol, [])[-5:]
    candle_data = [
        {"open": c.get("open", 0.0), "high": c.get("high", 0.0), "low": c.get("low", 0.0),
         "close": c.get("close", 0.0), "volume": c.get("volume", 0.0)}
        for c in candles
    ]
    orderbook = bot.orderbook_data.get(symbol, {"bids": [], "asks": []})
    ticker = bot.ticker_data.get(symbol, {"last_price": 0.0})
    return calculate_metrics(
        candle_data,
        orderbook,
        ticker,
        return_variations=True,
        symbol=symbol,
    )


@app.get("/settings")
def get_settings() -> Dict[str, Dict[str, Union[str, float, Dict[str, float]]]]:
    """Devuelve configuración actual + métricas live por símbolo (sin DB, usa estado en memoria)."""
    return {
        symbol: {
            "symbol": symbol,
            "capital": round(bot.capital, 4),
            "risk_factor": config.RISK_FACTOR,
            "min_trade_size": config.MIN_TRADE_SIZE,
            "max_trade_size": config.MAX_TRADE_SIZE,
            "metrics": _get_live_metrics(symbol),
        }
        for symbol in bot.symbols
    }

@app.get("/market_data/{symbol}")
def get_market_data(symbol: str, db: Session = Depends(get_db)) -> Dict[str, Union[str, List[Dict[str, float]]]]:
    """Últimas velas desde DB para un símbolo."""
    rows = db.query(MarketData).filter(MarketData.symbol == symbol).order_by(MarketData.timestamp.desc()).limit(5).all()
    return {
        "symbol": symbol,
        "timestamp": _safe_timestamp(rows[0].timestamp),
        "data": [_candle_row_to_dict(r) for r in rows],
    }
@app.get("/ticker/{symbol}")
def get_ticker(symbol: str) -> Dict[str, Union[str, float]]:
    """Último ticker desde datos en memoria (live)."""
    t = bot.ticker_data.get(symbol, {})
    data = {
        "last_price": _safe_float(t.get("last_price")),
        "volume_24h": _safe_float(t.get("volume_24h")),
        "high_24h": _safe_float(t.get("high_24h")),
        "low_24h": _safe_float(t.get("low_24h")),
    }
    return {
        "symbol": symbol,
        **data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

@app.get("/metrics/{symbol}")
def get_metrics(symbol: str, db: Session = Depends(get_db)) -> Dict[str, Union[str, Dict[str, float]]]:
    """Métricas calculadas desde DB (últimas 5 velas) + orderbook/ticker en memoria."""
    rows = db.query(MarketData).filter(MarketData.symbol == symbol).order_by(MarketData.timestamp.desc()).limit(5).all()
    candle_data = [_candle_row_to_dict(r) for r in rows]
    orderbook = bot.orderbook_data.get(symbol, {"bids": [], "asks": []})
    ticker = bot.ticker_data.get(symbol, {"last_price": 0.0})
    metrics = calculate_metrics(
        candle_data,
        orderbook,
        ticker,
        return_variations=True,
        symbol=symbol,
    )
    return {
        "symbol": symbol,
        "metrics": metrics,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

@app.get("/profit")
def get_profit() -> Dict[str, Union[str, float, int, Dict[str, Dict[str, Union[str, float, int]]]]]:
    """Resumen de P&L global y por símbolo desde posiciones en memoria."""
    positions = bot.positions
    symbols = bot.symbols

    total_trades = sum(len(positions.get(s, [])) for s in symbols)
    win_trades = sum(1 for s in symbols for t in positions.get(s, []) if t.get("profit_loss", 0) > 0)
    win_rate = (win_trades / total_trades * 100.0) if total_trades else 0.0

    by_symbol = {}
    for sym in symbols:
        stats = utils.pnl_stats(positions.get(sym, []))
        by_symbol[sym] = {
            "profit": stats["profit"],
            "loss": stats["loss"],
            "net_profit": stats["net"],
            "trade_count": len(positions.get(sym, [])),
        }

    total_profit = sum(s["profit"] for s in by_symbol.values())
    total_loss = sum(s["loss"] for s in by_symbol.values())

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "capital_inicial": bot.initial_capital,
        "capital_actual": round(bot.capital, 2),
        "total_pnl": round(bot.capital - bot.initial_capital, 2),
        "total_profit": round(total_profit, 2),
        "total_loss": round(total_loss, 2),
        "net_profit": round(total_profit + total_loss, 2),
        "win_rate": round(win_rate, 2),
        "by_symbol": by_symbol,
    }

@app.post("/config/update_thresholds")
def update_thresholds(egm_buy_threshold: float, egm_sell_threshold: float) -> Dict[str, str]:
    """Actualiza umbrales EGM de compra/venta."""
    config.EGM_BUY_THRESHOLD = egm_buy_threshold
    config.EGM_SELL_THRESHOLD = egm_sell_threshold
    logger.info(f"✅ Umbrales actualizados: buy={egm_buy_threshold}, sell={egm_sell_threshold}")
    return {"message": "Umbrales actualizados", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/orderbook/{symbol}")
def get_orderbook(symbol: str) -> Dict[str, Union[str, List[List[float]]]]:
    """Orderbook actual en memoria (bids/asks)."""
    ob = bot.orderbook_data.get(symbol, {"bids": [], "asks": []})
    return {
        "symbol": symbol,
        "bids": ob.get("bids", []),
        "asks": ob.get("asks", []),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

@app.get("/candles/{symbol}/{limit}")
def get_candles(symbol: str, limit: int = 5, db: Session = Depends(get_db)) -> Dict:
    """Velas paginadas desde DB."""
    rows = db.query(MarketData).filter(MarketData.symbol == symbol).order_by(MarketData.timestamp.desc()).limit(limit).all()
    return {
        "symbol": symbol,
        "candles": [
            {"timestamp": _safe_timestamp(r.timestamp), **_candle_row_to_dict(r)}
            for r in rows
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

@app.get("/trades/{symbol}")
def get_trades(symbol: str) -> Dict[str, Union[str, List[Dict[str, Any]]]]:
    """Trades ejecutados en memoria para un símbolo (RAM + persistencia en results.json)."""
    trades = bot.positions.get(symbol, [])
    return {
        "symbol": symbol,
        "trades": trades,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/execute_trade/{symbol}")
async def execute_trade(symbol: str, db: Session = Depends(get_db)) -> Dict[str, str]:
    await bot._execute_trade(symbol, db)
    return {"message": f"✅ Trade ejecutado para {symbol}", "timestamp": datetime.now(timezone.utc).isoformat()}



@app.get("/config")
def get_config() -> Dict[str, Union[str, float, int, bool]]:
    """Configuración actual del bot (valores de settings)."""
    return {
        "symbol": config.SYMBOL,
        "timeframe": config.TIMEFRAME,
        "order_type": config.ORDER_TYPE,
        "time_in_force": config.TIME_IN_FORCE,
        "orderbook_depth": config.ORDERBOOK_DEPTH,
        "bybit_env": config.BYBIT_ENV,
        "capital_usdt": config.CAPITAL_USDT,
        "risk_factor": config.RISK_FACTOR,
        "min_trade_size": config.MIN_TRADE_SIZE,
        "max_trade_size": config.MAX_TRADE_SIZE,
        "fee_rate": config.FEE_RATE,
        "tp_percentage": config.TP_PERCENTAGE,
        "sl_percentage": config.SL_PERCENTAGE,
        "egm_buy_threshold": config.EGM_BUY_THRESHOLD,
        "egm_sell_threshold": config.EGM_SELL_THRESHOLD,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

@app.post("/config/update_all")
def update_all_config(config_data: Dict[str, Union[str, float, int]]) -> Dict[str, str]:
    """Actualiza múltiples parámetros de configuración de forma segura."""
    if "capital_usdt" in config_data:
        val = _safe_float(config_data["capital_usdt"])
        if val > 0:
            config.CAPITAL_USDT = val
    if "risk_factor" in config_data:
        config.RISK_FACTOR = max(0.0, min(1.0, _safe_float(config_data["risk_factor"])))
    if "egm_buy_threshold" in config_data:
        config.EGM_BUY_THRESHOLD = _safe_float(config_data["egm_buy_threshold"])
    if "egm_sell_threshold" in config_data:
        config.EGM_SELL_THRESHOLD = _safe_float(config_data["egm_sell_threshold"])
    logger.info(f"✅ Configuración actualizada: {config_data}")
    return {"message": "Configuración actualizada", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/health")
async def health_check() -> Dict[str, Union[str, bool]]:
    qwen_ready = bool(os.getenv("DASHSCOPE_API_KEY", "").strip())
    return {
        "status": "healthy" if bot.running else "stopped",
        "running": bot.running,
        "qwen_configured": qwen_ready,
        "active_positions": sum(1 for p in bot.active_position.values() if p),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/check_reset")
def check_reset() -> Dict[str, Union[str, int, bool]]:
    total_trades = sum(len(bot.positions.get(s, [])) for s in bot.symbols)
    return {
        "has_trades": total_trades > 0,
        "trade_count": total_trades,
        "iterations": bot.iterations,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/intelligence/status")
def intelligence_status() -> Dict[str, Any]:
    """Human-in-the-loop checkpoint: expose AI layer state for judges and operators."""
    layer = getattr(bot, "intelligence", None)
    return {
        "qwen_model": getattr(getattr(layer, "qwen", None), "model", "qwen-plus"),
        "qwen_configured": bool(os.getenv("DASHSCOPE_API_KEY", "").strip()),
        "last_predictions": bot.last_predictions,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/start")
async def start_bot() -> Dict[str, str]:
    if not bot.running:
        bot.running = True
        asyncio.create_task(bot.start_async())
        return {"message": "✅ Bot iniciado", "timestamp": datetime.now(timezone.utc).isoformat()}
    return {"message": "⚠️ Bot ya está corriendo", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.post("/stop")
def stop_bot() -> Dict[str, str]:
    """Detiene el motor de trading de forma segura."""
    if bot.running:
        bot.stop()
        return {"message": "🛑 Bot detenido", "timestamp": datetime.now(timezone.utc).isoformat()}
    return {"message": "⚠️ Bot ya está detenido", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/status")
def get_status() -> Dict[str, Union[bool, int, List[str], str]]:
    """Estado actual del motor (running, iteraciones, símbolos)."""
    return {
        "running": bot.running,
        "iterations": bot.iterations,
        "symbols": bot.symbols,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

# ──────────────────────────────────────────────────────────────────────────────
# Professional entrypoint (supports: python -m scripts.nertz, python scripts/nertz.py, uvicorn)
# ──────────────────────────────────────────────────────────────────────────────
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8081"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "info")
server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level=LOG_LEVEL))


async def main(reset: bool = False, start_bot: bool = True) -> None:
    """Start trading engine and API together (production-style autopilot entrypoint)."""
    try:
        if reset:
            bot.reset_trades()
        bot.running = start_bot
        tasks = [server.serve()]
        if start_bot:
            tasks.insert(0, bot.start_async())
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("🛑 Shutdown requested")
        bot.stop()
    except Exception as exc:
        logger.error(f"❌ Critical error in main(): {exc}")
        await server.shutdown()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NertzMetalEngine — Autopilot Trading Agent")
    parser.add_argument("--reset", action="store_true", help="Reset trades and active positions before start")
    parser.add_argument("--no-bot", action="store_true", help="API only; start bot via POST /start")
    args = parser.parse_args()

    asyncio.run(main(reset=args.reset, start_bot=not args.no_bot))