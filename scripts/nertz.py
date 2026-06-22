import sys
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Union, List, Any, Coroutine

import aiohttp
import ntplib
import uvicorn
import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, Depends
from pybit.unified_trading import HTTP
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, JSON
from sqlalchemy.orm import sessionmaker, Session, declarative_base

from scripts import utils
from scripts.settings import ConfigSettings
from scripts.qwen_agent import QwenSignalAgent
from scripts.memory_agent import QwenMemoryAgent

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except AttributeError:
        pass  # Environments that don't support WindowsSelectorEventLoopPolicy


# Cargar configuración desde .env
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
config = ConfigSettings()

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

# Base de datos
DATABASE_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
os.makedirs(DATABASE_DIR, exist_ok=True)
DATABASE_URL = os.path.join(DATABASE_DIR, 'trading.db')
engine = create_engine(f"sqlite:///{DATABASE_URL}", connect_args={"check_same_thread": False})
Base = declarative_base()

def get_ntp_time() -> int:
    try:
        client = ntplib.NTPClient()
        response = client.request('pool.ntp.org')
        return int(response.tx_time * 1000)
    except Exception as e:
        logger.error(f"❌ Error al obtener tiempo NTP: {e}. Usando tiempo local.")
        return int(time.time() * 1000)

# Modelos de base de datos
class MarketData(Base):
    __tablename__ = "market_data"
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, index=True)
    symbol = Column(String(10), nullable=False)
    open = Column(Float, nullable=False, default=0.0)
    high = Column(Float, nullable=False, default=0.0)
    low = Column(Float, nullable=False, default=0.0)
    close = Column(Float, nullable=False, default=0.0)
    volume = Column(Float, nullable=False, default=0.0)

class Orderbook(Base):
    __tablename__ = "orderbook"
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    symbol = Column(String(10), nullable=False, index=True)
    bids = Column(JSON, nullable=False)
    asks = Column(JSON, nullable=False)

class MarketTicker(Base):
    __tablename__ = "market_ticker"
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    symbol = Column(String(10), nullable=False, index=True)
    last_price = Column(Float, nullable=False, default=0.0)
    volume_24h = Column(Float, nullable=False, default=0.0)
    high_24h = Column(Float, nullable=False, default=0.0)
    low_24h = Column(Float, nullable=False, default=0.0)

class Trade(Base):
    __tablename__ = "trades"
    id = Column(Integer, primary_key=True, index=True)
    trade_id = Column(Integer, nullable=False, unique=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    symbol = Column(String(10), nullable=False, index=True)
    action = Column(String, nullable=False)
    entry_price = Column(Float, nullable=False, default=0.0)
    exit_price = Column(Float, nullable=False, default=0.0)
    quantity = Column(Float, nullable=False, default=0.0)
    profit_loss = Column(Float, nullable=False, default=0.0)
    decision = Column(String, nullable=False)
    combined = Column(Float, nullable=False, default=0.0)
    ild = Column(Float, nullable=False, default=0.0)
    egm = Column(Float, nullable=False, default=0.0)
    rol = Column(Float, nullable=False, default=0.0)
    pio = Column(Float, nullable=False, default=0.0)
    ogm = Column(Float, nullable=False, default=0.0)
    risk_reward_ratio = Column(Float, nullable=False, default=1.5)

class ActivePosition(Base):
    __tablename__ = "active_positions"
    symbol = Column(String(10), primary_key=True, index=True)
    timestamp = Column(DateTime, nullable=False)
    action = Column(String, nullable=False) # "buy" or "sell"
    entry_price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    tp = Column(Float, nullable=False)
    sl = Column(Float, nullable=False)
    combined = Column(Float, nullable=False)
    ild = Column(Float, nullable=False)
    egm = Column(Float, nullable=False)
    rol = Column(Float, nullable=False)
    pio = Column(Float, nullable=False)
    ogm = Column(Float, nullable=False)

Base.metadata.create_all(bind=engine)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

async def fetch_data(session: aiohttp.ClientSession, url: str, params: Optional[Dict[str, str]] = None) -> Optional[Dict]:
    async with session.get(url, params=params) as response:
        if response.status == 200:
            return await response.json()
        logger.error(f"❌ Error en {url}: {response.status}")
        return None

def _update_orderbook(bid_dict: Dict[float, float], ask_dict: Dict[float, float], data: Dict) -> None:
    for price, qty in data["data"]["b"]:
        price = float(price)
        qty = float(qty)
        if qty > 0:
            bid_dict[price] = qty
        elif price in bid_dict:
            del bid_dict[price]
    for price, qty in data["data"]["a"]:
        price = float(price)
        qty = float(qty)
        if qty > 0:
            ask_dict[price] = qty
        elif price in ask_dict:
            del ask_dict[price]

class NertzMetalEngine:
    def __init__(self) -> None:
        self.timeframe = config.TIMEFRAME
        self.symbols = config.SYMBOL.split(",")
        self.capital = config.CAPITAL_USDT
        self.positions = {symbol: [] for symbol in self.symbols}
        self.active_position = {symbol: None for symbol in self.symbols}
        self.iterations = 0
        self.ws = None
        self.running = True
        self.orderbook_data = {symbol: {"bids": [], "asks": []} for symbol in self.symbols}
        self.ticker_data = {symbol: {"last_price": 0.0, "volume_24h": 0.0, "high_24h": 0.0, "low_24h": 0.0} for symbol in self.symbols}
        self.candles = {symbol: [] for symbol in self.symbols}
        self.trade_id_counter = self._load_initial_trade_id()
        self._load_positions()
        self._load_active_positions()
        self.last_orderbook_log = 0
        self.last_trade_time = {symbol: datetime.min.replace(tzinfo=timezone.utc) for symbol in self.symbols}
        self.last_kline_time = {symbol: 0 for symbol in self.symbols}
        self.session_start = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        # Nuevo: Buffer para trades
        self.trade_buffer = {symbol: [] for symbol in self.symbols}
        self.buffer_size = 10

        # Agentes de IA
        self.qwen_agent = QwenSignalAgent()
        self.memory_agent = QwenMemoryAgent()

        # Reutilización de sesión Bybit HTTP
        self.bybit_session = None
        if config.BYBIT_API_KEY and config.BYBIT_API_SECRET:
            logger.info(f"Initializing reused Bybit HTTP session (env: {config.BYBIT_ENV})")
            self.bybit_session = HTTP(
                testnet=False,
                demo=(config.BYBIT_ENV == "demo"),
                api_key=config.BYBIT_API_KEY,
                api_secret=config.BYBIT_API_SECRET
            )

    @staticmethod
    def _load_initial_trade_id () -> int:
            with SessionLocal () as db:
                last_trade = db.query (Trade.trade_id).order_by (Trade.trade_id.desc ()).first ()
                return last_trade [0] + 1 if last_trade else 1

    def _load_positions(self) -> None:
        with SessionLocal() as db:
            for symbol in self.symbols:
                trades = db.query(Trade).filter_by(symbol=symbol).order_by(Trade.timestamp.desc()).all()
                self.positions[symbol] = [{
                    "trade_id": t.trade_id,
                    "timestamp": t.timestamp.isoformat(),
                    "symbol": t.symbol,
                    "action": t.action,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "quantity": t.quantity,
                    "profit_loss": t.profit_loss,
                    "decision": t.decision,
                    "combined": t.combined,
                    "ild": t.ild,
                    "egm": t.egm,
                    "rol": t.rol,
                    "pio": t.pio,
                    "ogm": t.ogm,
                    "risk_reward_ratio": t.risk_reward_ratio
                } for t in trades]

    def _load_active_positions(self) -> None:
        with SessionLocal() as db:
            for symbol in self.symbols:
                pos = db.query(ActivePosition).filter_by(symbol=symbol).first()
                if pos:
                    self.active_position[symbol] = {
                        "symbol": pos.symbol,
                        "timestamp": pos.timestamp.isoformat(),
                        "action": pos.action,
                        "entry_price": pos.entry_price,
                        "quantity": pos.quantity,
                        "tp": pos.tp,
                        "sl": pos.sl,
                        "combined": pos.combined,
                        "ild": pos.ild,
                        "egm": pos.egm,
                        "rol": pos.rol,
                        "pio": pos.pio,
                        "ogm": pos.ogm
                    }
                    logger.info(f"💾 Posición activa cargada desde DB para {symbol}: {pos.action.upper()} @ {pos.entry_price:.2f}, TP={pos.tp:.2f}, SL={pos.sl:.2f}")
                else:
                    self.active_position[symbol] = None

    async def fetch_initial_data(self) -> None:
        async with aiohttp.ClientSession() as session:
            tasks = [self._fetch_symbol_data(session, symbol) for symbol in self.symbols]
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch_symbol_data(self, session: aiohttp.ClientSession, symbol: str) -> None:
        try:
            kline_url = f"{BASE_URL}/v5/market/kline"
            params = {"category": "spot", "symbol": symbol, "interval": self.timeframe.replace("m", ""), "limit": "50"}
            kline_response = await fetch_data(session, kline_url, params)
            if kline_response and "result" in kline_response and "list" in kline_response["result"]:
                with SessionLocal() as db:
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

                    # Convertir a diccionarios para evitar problemas con sesiones
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
        except Exception as e:
            logger.error(f"❌ Fetch inicial falló para {symbol}: {e}")

    async def start_async(self) -> None:
        logger.info(f"🔥 Iniciando bot para {self.symbols}")
        max_attempts = 5
        for attempt in range(max_attempts):
            if not self.running:
                logger.info("🛑 Bot detenido antes de iniciar.")
                break
            try:
                logger.info(f"Intento {attempt + 1}/{max_attempts} para obtener datos iniciales...")
                await self.fetch_initial_data()
                logger.info("✅ Datos iniciales obtenidos, conectando al WebSocket...")
                await self._connect_websocket_async()
                break
            except Exception as e:
                logger.error(f"❌ Error al iniciar (intento {attempt + 1}/{max_attempts}): {e}")
                if attempt < max_attempts - 1:
                    await asyncio.sleep(5)
                else:
                    logger.critical("❌ Máximo de intentos alcanzado. Deteniendo.")
                    self.stop()

    async def _connect_websocket_async(self) -> None:
        while self.running:
            try:
                async with websockets.connect(WS_URL) as ws:
                    self.ws = ws
                    logger.info("🌐 WebSocket abierto")
                    await self._resubscribe_async()
                    async for message in ws:
                        await self._on_message(ws, message)
            except websockets.ConnectionClosed as e:
                logger.warning(f"⚠️ WebSocket cerrado: {e}, intentando reconectar en 5s...")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"❌ Error en WebSocket: {e}")
                await asyncio.sleep(5)

    async def _resubscribe_async(self) -> None:
        interval = self.timeframe.replace("m", "")
        for symbol in self.symbols:
            subscription = {"op": "subscribe", "args": [f"kline.{interval}.{symbol}", f"orderbook.50.{symbol}", f"tickers.{symbol}"]}
            if self.ws:
                await self.ws.send(json.dumps(subscription))
                logger.info(f"📡 Suscrito a {symbol}")

    async def _on_message(self, ws: Any, message: Any) -> None:
        with SessionLocal() as db:
            try:
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
            except json.JSONDecodeError as e:
                logger.error(f"❌ Error de decodificación JSON: {e}")
            except Exception as e:
                logger.error(f"❌ Error inesperado en mensaje: {e}")

    async def _handle_kline(self, symbol: str, kline: Dict, db: Session) -> None:
        try:
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

                # Convertir a diccionario para evitar problemas con sesiones
                self.candles[symbol].append({
                    "timestamp": candle.timestamp.isoformat(),
                    "symbol": candle.symbol,
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume
                })
                self.candles[symbol] = self.candles[symbol][-50:]  # Mantener las últimas 50 velas
                self.last_kline_time[symbol] = int(timestamp_value)
                logger.info(f"⚡ Kline para {symbol}: Close={candle.close}, Volume={candle.volume}")
                logger.info(f"📈 Acumulados {len(self.candles[symbol])} velas para {symbol}")

                await self._execute_trade(symbol, db)
        except Exception as e:
            logger.error(f"❌ Error en _handle_kline para {symbol}: {e}")

    async def _handle_orderbook(self, symbol: str, data: Dict, db: Session) -> None:
        try:
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
        except Exception as e:
            logger.error(f"❌ Error en _handle_orderbook para {symbol}: {e}")

    async def _store_orderbook(self, symbol: str, db: Session) -> None:
        try:
            orderbook = Orderbook(
                timestamp=datetime.now(timezone.utc), symbol=symbol,
                bids=json.dumps(self.orderbook_data[symbol]["bids"]),
                asks=json.dumps(self.orderbook_data[symbol]["asks"])
            )
            db.add(orderbook)
            db.commit()
            if time.time() - self.last_orderbook_log >= 5:
                logger.info(f"🤘 Orderbook guardado para {symbol}: Bids={len(self.orderbook_data[symbol]['bids'])}, Asks={len(self.orderbook_data[symbol]['asks'])}")
                self.last_orderbook_log = time.time()
        except Exception as e:
            logger.error(f"❌ Error al guardar orderbook para {symbol}: {e}")

    async def _handle_ticker(self, symbol: str, ticker: Dict, db: Session) -> None:
        try:
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
                try:
                    ticker_values[key] = float(value) if value else 0.0
                except (ValueError, TypeError):
                    ticker_values[key] = 0.0

            if ticker_values.get("usdIndexPrice", 0.0) <= 0:
                ticker_values["usdIndexPrice"] = self.ticker_data[symbol].get("usd_index_price", 0.0)

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
            await self._check_active_positions(symbol, self.ticker_data[symbol]['last_price'], db)
        except Exception as e:
            logger.error(f"❌ Error en _handle_ticker para {symbol}: {e}")

    async def _determine_decision(self, symbol: str, metrics: Dict[str, float]) -> str:
        egm = metrics.get("egm", 0.0)
        combined = metrics.get("combined", 0.0)
        logger.info(
            f"🔍 {symbol}: EGM={egm:.4f}, Combined={combined:.4f}, Buy Threshold={config.EGM_BUY_THRESHOLD}, Sell Threshold={config.EGM_SELL_THRESHOLD}"
        )
        
        local_decision = "hold"
        if egm >= config.EGM_BUY_THRESHOLD or combined >= 1.0:
            local_decision = "buy"
        elif egm <= config.EGM_SELL_THRESHOLD or combined <= -1.0:
            local_decision = "sell"
            
        if local_decision == "hold":
            return "hold"
            
        # Validación con Qwen Signal Agent y Memory Agent
        try:
            history_context = self.memory_agent.get_recent_context(symbol)
            validation = await self.qwen_agent.validate_signal(symbol, local_decision, metrics, history_context)
            qwen_decision = validation.get("action", "hold").lower()
            confidence = validation.get("confidence", 0.0)
            reason = validation.get("reason", "Sin justificación")
            
            if qwen_decision == local_decision:
                logger.info(f"✅ [Qwen] Señal de {local_decision.upper()} VALIDADA con confianza {confidence:.2f} para {symbol}. Motivo: {reason}")
                return local_decision
            else:
                logger.info(f"❌ [Qwen] Señal de {local_decision.upper()} RECHAZADA (Qwen sugirió {qwen_decision.upper()}) para {symbol}. Motivo: {reason}")
                return "hold"
        except Exception as e:
            logger.error(f"❌ Error al validar señal con Qwen: {e}", exc_info=True)
            return "hold"

    async def _execute_trade (self, symbol: str, db: Session) -> None:
        try:
            # Bloquear nuevas órdenes si ya hay una posición activa
            if self.active_position.get(symbol) is not None:
                logger.debug(f"🔒 Posición activa (LOCKED) para {symbol}. Bloqueando nuevas señales.")
                return

            # Forzar la generación del archivo JSON inicial
            await self._save_results (symbol, None)

            candles = self.candles.get (symbol, [])
            if len (candles) < 5:
                logger.warning (f"⚠️ No hay suficientes velas ({len (candles)}/5) para {symbol}")
                return

            current_time = datetime.now (timezone.utc)
            cooldown = timedelta (seconds=config.DEFAULT_SLEEP_TIME)
            last_trade_time = self.last_trade_time.get (symbol, datetime.min.replace (tzinfo=timezone.utc))
            if current_time <= last_trade_time + cooldown:
                logger.debug (f"⏳ Cooldown activo para {symbol}, esperando hasta {last_trade_time + cooldown}")
                return

            candle_data = [
                {"open": c ["open"], "high": c ["high"], "low": c ["low"], "close": c ["close"], "volume": c ["volume"]}
                for c in candles
            ]
            orderbook = self.orderbook_data.get (symbol, {"bids": [], "asks": []})
            ticker = self.ticker_data.get (symbol, {"last_price": 0.0})

            metrics = utils.calculate_metrics (candle_data, orderbook, ticker)
            logger.info (
                f"📊 Métricas calculadas para {symbol}: pio={metrics.get ('pio', 0)}, ild={metrics.get ('ild', 0)}, egm={metrics.get ('egm', 0)}, rol={metrics.get ('rol', 0)}, combined={metrics.get ('combined', 0)}")

            decision = await self._determine_decision (symbol, metrics)
            if decision == "hold":
                logger.debug (f"🤖 Decisión de hold para {symbol}")
                return

            risk_per_trade = self.capital * config.RISK_FACTOR
            volatility = metrics.get ("volatility", 0.01)
            if volatility <= 0:
                logger.warning (f"⚠️ Volatilidad inválida ({volatility}) para {symbol}")
                volatility = 0.01

            last_price = ticker.get ("last_price", 0.0)
            if last_price <= 0:
                logger.error (f"❌ Precio inválido ({last_price}) para {symbol}")
                return

            quantity = risk_per_trade / (volatility * last_price)
            quantity = max (min (quantity, config.MAX_TRADE_SIZE), config.MIN_TRADE_SIZE)

            trade_value = quantity * last_price
            if trade_value > self.capital:
                logger.warning (f"⚠️ Cantidad excesiva ({trade_value:.2f}) para {symbol}. Ajustando...")
                quantity = (self.capital * 0.1) / last_price
                if quantity < config.MIN_TRADE_SIZE:
                    logger.warning (f"⚠️ Cantidad ajustada ({quantity}) por debajo del mínimo. Saltando trade.")
                    return

            entry_price = last_price
            strategy = utils.TpslStrategy ()
            tp_sl = strategy.calculate_take_profit_stop_loss (entry_price, decision, volatility)
            tp, sl = tp_sl if tp_sl != (0.0, 0.0) else (
            entry_price * (1 + config.TP_PERCENTAGE), entry_price * (1 - config.SL_PERCENTAGE))

            order_result = await self._place_order (symbol, decision, quantity, entry_price, tp, sl)
            if not order_result.get ("success", False):
                logger.error (
                    f"❌ Fallo al colocar orden para {symbol}: {order_result.get ('message', 'Error desconocido')}")
                return

            # Guardar posición abierta en SQLite (tabla active_positions)
            active_pos = ActivePosition(
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                action=decision,
                entry_price=entry_price,
                quantity=quantity,
                tp=tp,
                sl=sl,
                combined=metrics.get("combined", 0.0),
                ild=metrics.get("ild", 0.0),
                egm=metrics.get("egm", 0.0),
                rol=metrics.get("rol", 0.0),
                pio=metrics.get("pio", 0.0),
                ogm=metrics.get("ogm", 0.0)
            )
            db.add(active_pos)
            db.commit()

            self.active_position[symbol] = {
                "symbol": symbol,
                "timestamp": active_pos.timestamp.isoformat(),
                "action": decision,
                "entry_price": entry_price,
                "quantity": quantity,
                "tp": tp,
                "sl": sl,
                "combined": active_pos.combined,
                "ild": active_pos.ild,
                "egm": active_pos.egm,
                "rol": active_pos.rol,
                "pio": active_pos.pio,
                "ogm": active_pos.ogm
            }

            logger.info(f"🔒 Posición abierta (LOCKED) para {symbol}: {decision.upper()} @ {entry_price:.2f}, TP={tp:.2f}, SL={sl:.2f}, Qty={quantity:.6f}")

            self.iterations += 1
            if config.MAX_ITERATIONS > 0 and self.iterations >= config.MAX_ITERATIONS:
                logger.info ("🏁 Máximo de iteraciones alcanzado. Deteniendo bot.")
                self.stop ()
        except Exception as e:
            logger.error (f"❌ Error en _execute_trade para {symbol}: {e}")

    async def _check_active_positions(self, symbol: str, current_price: float, db: Session) -> None:
        pos = self.active_position.get(symbol)
        if pos is None:
            return

        action = pos["action"]
        tp = pos["tp"]
        sl = pos["sl"]
        entry_price = pos["entry_price"]
        qty = pos["quantity"]

        tp_hit = False
        sl_hit = False

        if action == "buy":
            if current_price >= tp:
                tp_hit = True
            elif current_price <= sl:
                sl_hit = True
        elif action == "sell":
            if current_price <= tp:
                tp_hit = True
            elif current_price >= sl:
                sl_hit = True

        if not (tp_hit or sl_hit):
            return

        exit_reason = "TP hit" if tp_hit else "SL hit"
        exit_price = current_price
        exit_action = "sell" if action == "buy" else "buy"

        logger.info(f"⚡ Posición para {symbol} activa cruza límites: {exit_reason} (Precio: {current_price:.2f}, TP={tp:.2f}, SL={sl:.2f})")

        # Colocar orden de salida
        order_result = await self._place_order(symbol, exit_action, qty, exit_price, 0.0, 0.0)
        if not order_result.get("success", False):
            logger.error(f"❌ Fallo al colocar orden de salida para {symbol}: {order_result.get('message', 'Error desconocido')}")
            return

        # Calcular P&L realizado real (neto de comisiones)
        if action == "buy":
            gross_pnl = (exit_price - entry_price) * qty
        else:
            gross_pnl = (entry_price - exit_price) * qty

        # Comisiones (entrada + salida)
        entry_fee = entry_price * qty * config.FEE_RATE
        exit_fee = exit_price * qty * config.FEE_RATE
        total_fee = entry_fee + exit_fee
        net_realized_pnl = gross_pnl - total_fee

        # Actualizar capital
        new_capital = self.capital + net_realized_pnl
        if new_capital > 1e15 or new_capital < 0:
            logger.error(f"❌ Capital anormal ({new_capital:.2f}) para {symbol} tras cierre de posición. Reseteando a {config.CAPITAL_USDT}")
            self.capital = config.CAPITAL_USDT
        else:
            self.capital = new_capital

        # Guardar en base de datos de trades
        self.trade_id_counter += 1
        denominator = abs(entry_price - sl)
        r_r_ratio = abs(tp - entry_price) / denominator if denominator > 0 else 1.5

        trade = Trade(
            trade_id=self.trade_id_counter - 1,
            timestamp=datetime.now(timezone.utc),
            symbol=symbol,
            action=action,
            entry_price=entry_price,
            exit_price=exit_price,
            quantity=qty,
            profit_loss=net_realized_pnl,
            decision=action,
            combined=pos["combined"],
            ild=pos["ild"],
            egm=pos["egm"],
            rol=pos["rol"],
            pio=pos["pio"],
            ogm=pos["ogm"],
            risk_reward_ratio=r_r_ratio
        )
        db.add(trade)
        db.commit()

        trade_dict = {
            "trade_id": trade.trade_id,
            "timestamp": trade.timestamp.isoformat(),
            "symbol": trade.symbol,
            "action": trade.action,
            "entry_price": trade.entry_price,
            "exit_price": trade.exit_price,
            "quantity": trade.quantity,
            "profit_loss": trade.profit_loss,
            "decision": trade.decision,
            "combined": trade.combined,
            "ild": trade.ild,
            "egm": trade.egm,
            "rol": trade.rol,
            "pio": trade.pio,
            "ogm": trade.ogm,
            "risk_reward_ratio": trade.risk_reward_ratio
        }

        self.positions.setdefault(symbol, []).append(trade_dict)
        self.trade_buffer[symbol].append(trade_dict)
        self.last_trade_time[symbol] = datetime.now(timezone.utc)
        self.active_position[symbol] = None

        # Eliminar de active_positions en SQLite
        db.query(ActivePosition).filter_by(symbol=symbol).delete()
        db.commit()

        logger.info(f"💰 Posición CERRADA para {symbol} por {exit_reason} @ {exit_price:.2f}. P&L realizado (neto): {net_realized_pnl:.2f} USDT, Capital: {self.capital:.2f} USDT")

        # Guardar resultados
        try:
            await self._save_results(symbol, trade)
        except Exception as e:
            logger.error(f"❌ Error al guardar resultados tras cierre de posición para {symbol}: {e}")

    async def _execute_trade_on_ticker (self, symbol: str, db: Session) -> None:
        current_time = int (time.time () * 1000)
        if current_time - self.last_kline_time.get (symbol, 0) > 60000:
            logger.debug (f"🔄 Sin kline reciente para {symbol}, usando ticker para trade")
            await self._execute_trade (symbol, db)

    async def _place_order (self, symbol: str, action: str, quantity: float, price: float, tp: float, sl: float) -> \
            None | dict [str, str | None | bool] | dict [str, str | None | bool] | dict [str, str | None | bool] | \
            dict [str, str | None | bool] | dict [str, str | None | bool]:
        """
        Envía una orden a Bybit con soporte para TP y SL.

        Args:
            symbol (str): Par de trading (ej. "BTCUSDT").
            action (str): Acción ("buy" o "sell").
            quantity (float): Cantidad a negociar.
            price (float): Precio (solo para órdenes limit).
            tp (float): Take Profit price (no enviado para spot actualmente).
            sl (float): Stop Loss price (no enviado para spot actualmente).

        Returns:
            Dict[str, Any]: Resultado de la orden {"success": bool, "order_id": str|None, "message": str|None}.
        """
        max_retries = 3
        max_delay = 8  # Máximo de 8 segundos de retraso entre reintentos

        # Validaciones básicas
        if not symbol or not isinstance (symbol, str):
            logger.error (f"❌ Símbolo inválido: {symbol}")
            return {"success": False, "order_id": None, "message": "Símbolo inválido"}
        if action.lower () not in ["buy", "sell"]:
            logger.error (f"❌ Acción inválida: {action}")
            return {"success": False, "order_id": None, "message": "Acción debe ser 'buy' o 'sell'"}
        if quantity <= 0 or not isinstance (quantity, (int, float)):
            logger.error (f"❌ Cantidad inválida: {quantity}")
            return {"success": False, "order_id": None, "message": "Cantidad debe ser positiva"}
        if price <= 0 and config.ORDER_TYPE.lower () == "limit":
            logger.error (f"❌ Precio inválido: {price}")
            return {"success": False, "order_id": None, "message": "Precio debe ser positivo para órdenes limit"}
        if tp <= 0 or sl <= 0:
            logger.warning (f"⚠ TP o SL inválidos ({tp}, {sl}), se omitirán en la orden")

        for attempt in range (max_retries):
            try:
                if not config.BYBIT_API_KEY or not config.BYBIT_API_SECRET:
                    logger.warning (f"⚠️ Sin API keys, simulando orden: {action.upper ()} {quantity:.6f} {symbol}")
                    return {"success": True, "order_id": f"sim-{self.trade_id_counter}", "message": "Orden simulada"}

                # Reutilizar sesión a nivel de clase
                if not self.bybit_session:
                    self.bybit_session = HTTP(
                        testnet=False,
                        demo=(config.BYBIT_ENV == "demo"),
                        api_key=config.BYBIT_API_KEY,
                        api_secret=config.BYBIT_API_SECRET
                    )

                session = self.bybit_session
                side = "Buy" if action.lower () == "buy" else "Sell"
                order_type = config.ORDER_TYPE
                time_in_force = config.TIME_IN_FORCE

                order_params = {
                    "category": "spot",
                    "symbol": symbol,
                    "side": side,
                    "orderType": order_type,
                    "qty": f"{quantity:.6f}",
                    "timeInForce": time_in_force,
                }
                if order_type.lower () == "limit":
                    order_params ["price"] = f"{price:.2f}"

                # Nota: TP y SL no se envían para spot hasta que implementemos órdenes condicionales
                if tp > 0 and sl > 0:
                    logger.info (f"📌 TP={tp:.2f} y SL={sl:.2f} calculados pero no enviados (no soportados en spot)")
                else:
                    logger.debug ("⚠ No se definieron TP o SL para la orden")

                response = session.place_order (**order_params)
                logger.debug (f"📥 Respuesta de pybit: {json.dumps (response, indent=2)}")

                # Handle case where response might be a tuple (status_code, response_dict, request_headers) in some pybit versions
                if isinstance(response, tuple):
                    # Extract the actual response dictionary (usually the second element)
                    resp_data = response[1] if len(response) > 1 else response[0]
                else:
                    resp_data = response
                
                if isinstance(resp_data, dict) and resp_data.get("retCode") == 0:
                    order_id = resp_data["result"]["orderId"]
       
                    logger.info (
                        f"✅ Orden colocada: {symbol} {side} {quantity:.6f} @ {price if order_type.lower () == 'limit' else 'market'}, OrderID={order_id}"
                    )
                    return {"success": True, "order_id": order_id, "message": "Orden exitosa"}
                else:
                    # Handle response appropriately whether it's a dict or tuple
                    if isinstance(response, tuple):
                        # Extract error info from the response tuple
                        if len(response) > 1 and isinstance(response[1], dict):
                            ret_code = response[1].get('retCode', 'Unknown')
                            ret_msg = response[1].get('retMsg', 'Unknown')
                        else:
                            ret_code = 'Unknown'
                            ret_msg = 'Unknown'
                    else:
                        ret_code = response.get('retCode', 'Unknown') if isinstance(response, dict) else 'Unknown'
                        ret_msg = response.get('retMsg', 'Unknown') if isinstance(response, dict) else 'Unknown'
                    
                    logger.error (
                        f"❌ Error al colocar orden: retCode={ret_code}, retMsg={ret_msg}"
                    )
                    raise Exception (ret_msg if ret_msg != 'Unknown' else "Error desconocido")

            except Exception as e:
                error_msg = str (e)
                logger.error (
                    f"❌ Fallo al colocar orden para {symbol} (intento {attempt + 1}/{max_retries}): {error_msg}")
                if attempt < max_retries - 1:
                    delay = min (2 ** attempt, max_delay)  # Retraso exponencial con límite
                    logger.info (f"⏳ Reintentando en {delay} segundos...")
                    await asyncio.sleep (delay)
                else:
                    return {"success": False, "order_id": None, "message": error_msg}

        return {"success": False, "order_id": None, "message": "Máximo de reintentos alcanzado"}

    def reset_trades(self) -> None:
        self.positions = {symbol: [] for symbol in self.symbols}
        self.active_position = {symbol: None for symbol in self.symbols}
        self.trade_id_counter = 1
        with SessionLocal() as db:
            db.query(Trade).delete()
            db.query(ActivePosition).delete()
            db.commit()
        logger.info("🧹 Trades y posiciones activas reseteados")

    async def _save_results(self, symbol: Optional[str], trade_result: Optional[Trade] = None) -> None:
        try:
            # Usar el primer símbolo disponible si 'symbol' es None
            if symbol is None:
                symbol = self.symbols[0] if self.symbols else "BTCUSDT"

            total_profit = sum(trade["profit_loss"] for sym in self.symbols for trade in self.positions.get(sym, []) if trade["profit_loss"] > 0)
            total_loss = sum(trade["profit_loss"] for sym in self.symbols for trade in self.positions.get(sym, []) if trade["profit_loss"] < 0)
            total_trades = sum(len(self.positions.get(sym, [])) for sym in self.symbols)
            profit_by_symbol = {sym: round(sum(trade["profit_loss"] for trade in self.positions[sym] if trade["profit_loss"] > 0), 2) for sym in self.symbols}
            loss_by_symbol = {sym: round(sum(trade["profit_loss"] for trade in self.positions[sym] if trade["profit_loss"] < 0), 2) for sym in self.symbols}

            net_profit = total_profit + total_loss
            win_rate = (len([trade for sym in self.symbols for trade in self.positions.get(sym, []) if trade["profit_loss"] > 0]) / total_trades) if total_trades > 0 else 0
            avg_profit_per_trade = net_profit / total_trades if total_trades > 0 else 0

            results = {
                "metadata": {
                    "session_start": self.session_start,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "capital_inicial": config.CAPITAL_USDT,
                    "capital_actual": self.capital,
                    "capital_final": self.capital,
                    "total_pnl": round(self.capital - config.CAPITAL_USDT, 2),
                    "total_trades": total_trades,
                    "iterations": self.iterations,
                    "running": self.running
                },
                "summary": {
                    "total_profit": round(total_profit, 2),
                    "total_loss": round(total_loss, 2),
                    "net_profit": round(net_profit, 2),
                    "win_rate": round(win_rate * 100, 2),
                    "avg_profit_per_trade": round(avg_profit_per_trade, 2)
                },
                "by_symbol": {
                    sym: {
                        "profit": profit_by_symbol[sym],
                        "loss": loss_by_symbol[sym],
                        "net_profit": round(profit_by_symbol[sym] + loss_by_symbol[sym], 2),
                        "trade_count": len(self.positions[sym])
                    } for sym in self.symbols
                },
                "trades": {sym: self.positions[sym] for sym in self.symbols}
            }
            if trade_result:
                results["metadata"]["last_trade_timestamp"] = trade_result.timestamp.isoformat()
                results["last_trade"] = {
                    "trade_id": trade_result.trade_id,
                    "timestamp": trade_result.timestamp.isoformat(),
                    "symbol": trade_result.symbol,
                    "action": trade_result.action,
                    "entry_price": trade_result.entry_price,
                    "exit_price": trade_result.exit_price,
                    "quantity": trade_result.quantity,
                    "profit_loss": trade_result.profit_loss,
                    "decision": trade_result.decision,
                    "combined": trade_result.combined,
                    "ild": trade_result.ild,
                    "egm": trade_result.egm,
                    "rol": trade_result.rol,
                    "pio": trade_result.pio,
                    "ogm": trade_result.ogm,
                    "risk_reward_ratio": trade_result.risk_reward_ratio
                }

            log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
            utils.save_results(results, log_dir, self.session_start)
            logger.info(f"📊 Resultados guardados: Total PNL={round(net_profit, 2)} USDT, Capital={self.capital:.2f} USDT")
        except Exception as e:
            logger.error(f"❌ Error al guardar resultados: {e}")

        # Limpiar el buffer después de guardar
        self.trade_buffer = {sym: [] for sym in self.symbols}

    def stop(self) -> None:
        self.running = False
        if self.ws:
            asyncio.create_task(self.ws.close())
        # Guardar cualquier trade restante en el buffer antes de detener
        if any(self.trade_buffer.values()):
            # Pass a valid symbol instead of None, or handle the None case in _save_results
            # We'll call _save_results for the first available symbol in the buffer
            for symbol in self.symbols:
                if self.trade_buffer.get(symbol):
                    asyncio.create_task(self._save_results(symbol, None))
                    break
            else:
                # If no symbols have buffered trades, just call with first symbol
                asyncio.create_task(self._save_results(self.symbols[0] if self.symbols else "BTCUSDT", None))
        logger.info("🛑 Bot detenido.")

app = FastAPI()
bot = NertzMetalEngine()

@app.get("/settings")
async def get_settings() -> Dict[str, Dict[str, Union[str, float, Dict[str, float]]]]:
    settings = {
        symbol: {
            "symbol": symbol,
            "capital": bot.capital,
            "risk_factor": config.RISK_FACTOR,
            "min_trade_size": config.MIN_TRADE_SIZE,
            "max_trade_size": config.MAX_TRADE_SIZE,
            "metrics": await get_metrics(symbol, next(get_db()))
        } for symbol in bot.symbols
    }
    return settings

@app.get("/market_data/{symbol}")
async def get_market_data(symbol: str, db: Session = Depends(get_db)) -> Dict[str, Union[str, List[Dict[str, Union[str, float]]]]]:
    candles = db.query(MarketData).filter(MarketData.symbol == symbol).order_by(MarketData.timestamp.desc()).limit(5).all()
    return {
        "symbol": symbol,
        "candles": [
            {"timestamp": c.timestamp.isoformat(), "open": float(c.open) if c.open is not None else 0.0, "high": float(c.high) if c.high is not None else 0.0, "low": float(c.low) if c.low is not None else 0.0,
             "close": float(c.close) if c.close is not None else 0.0, "volume": float(c.volume) if c.volume is not None else 0.0} for c in candles
        ],
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/ticker/{symbol}")
async def get_ticker(symbol: str, db: Session = Depends(get_db)) -> Dict[str, Union[str, float]]:
    ticker = db.query(MarketTicker).filter(MarketTicker.symbol == symbol).order_by(MarketTicker.timestamp.desc()).first()
    return {
        "symbol": symbol,
        "last_price": ticker.last_price if ticker else 0.0,
        "volume_24h": ticker.volume_24h if ticker else 0.0,
        "high_24h": ticker.high_24h if ticker else 0.0,
        "low_24h": ticker.low_24h if ticker else 0.0,
        "timestamp": ticker.timestamp.isoformat() if ticker else datetime.now(timezone.utc).isoformat()
    }

@app.get("/metrics/{symbol}")
async def get_metrics(symbol: str, db: Session = Depends(get_db)) -> Dict[str, Union[str, Dict[str, float]]]:
    candles = db.query(MarketData).filter(MarketData.symbol == symbol).order_by(MarketData.timestamp.desc()).limit(5).all()
    # Convertir explícitamente los atributos a float para evitar errores de tipado
    candle_data = [
        {"open": float(c.open) if c.open is not None else 0.0, "high": float(c.high) if c.high is not None else 0.0, "low": float(c.low) if c.low is not None else 0.0, "close": float(c.close) if c.close is not None else 0.0, "volume": float(c.volume) if c.volume is not None else 0.0}
        for c in candles
    ]
    orderbook = bot.orderbook_data.get(symbol, {"bids": [], "asks": []})
    ticker = bot.ticker_data.get(symbol, {"last_price": 0.0})
    metrics = utils.calculate_metrics(candle_data, orderbook, ticker)
    return {
        "symbol": symbol,
        "metrics": metrics,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/profit")
async def get_profit(db: Session = Depends(get_db)) -> Dict[str, Union[str, float, int, Dict[str, Dict[str, Union[str, float, int]]]]]:
    total_profit = sum(trade["profit_loss"] for symbol in bot.symbols for trade in bot.positions.get(symbol, []) if trade["profit_loss"] > 0)
    total_loss = sum(trade["profit_loss"] for symbol in bot.symbols for trade in bot.positions.get(symbol, []) if trade["profit_loss"] < 0)
    total_trades = sum(len(bot.positions.get(symbol, [])) for symbol in bot.symbols)
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "capital_inicial": config.CAPITAL_USDT,
        "capital_actual": bot.capital,
        "total_pnl": round(bot.capital - config.CAPITAL_USDT, 2),
        "total_profit": round(total_profit, 2),
        "total_loss": round(total_loss, 2),
        "net_profit": round(total_profit + total_loss, 2),
        "win_rate": round((len([t for s in bot.symbols for t in bot.positions.get(s, []) if t["profit_loss"] > 0]) / (total_trades or 1)) * 100, 2),
        "by_symbol": {
            symbol: {
                "profit": round(sum(t["profit_loss"] for t in bot.positions.get(symbol, []) if t["profit_loss"] > 0), 2),
                "loss": round(sum(t["profit_loss"] for t in bot.positions.get(symbol, []) if t["profit_loss"] < 0), 2),
                "net_profit": round(sum(t["profit_loss"] for t in bot.positions.get(symbol, [])), 2),
                "trade_count": len(bot.positions.get(symbol, []))
            } for symbol in bot.symbols
        }
    }

@app.post("/config/update_thresholds")
async def update_thresholds(egm_buy_threshold: float, egm_sell_threshold: float) -> Dict[str, str]:
    config.EGM_BUY_THRESHOLD = egm_buy_threshold
    config.EGM_SELL_THRESHOLD = egm_sell_threshold
    logger.info(f"✅ Umbrales actualizados: buy={egm_buy_threshold}, sell={egm_sell_threshold}")
    return {"message": "Umbrales actualizados"}

@app.get("/orderbook/{symbol}")
async def get_orderbook(symbol: str, db: Session = Depends(get_db)) -> Dict[str, Union[str, List[List[str]]]]:
    orderbook = bot.orderbook_data.get(symbol, {"bids": [], "asks": []})
    return {
        "symbol": symbol,
        "bids": orderbook["bids"],
        "asks": orderbook["asks"],
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/candles/{symbol}/{limit}")
async def get_candles(symbol: str, limit: int = 5, db: Session = Depends(get_db)) -> Dict[str, Union[str, List[Dict[str, Union[str, float]]]]]:
    candles = db.query(MarketData).filter(MarketData.symbol == symbol).order_by(MarketData.timestamp.desc()).limit(limit).all()
    return {
        "symbol": symbol,
        "candles": [
            {"timestamp": c.timestamp.isoformat(), "open": float(c.open), "high": float(c.high), "low": float(c.low),
             "close": float(c.close), "volume": float(c.volume)} for c in candles
        ],
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/trades/{symbol}")
async def get_trades(symbol: str, db: Session = Depends(get_db)) -> Dict[str, Union[str, List[Dict[str, Union[str, float, int]]]]]:
    trades = bot.positions.get(symbol, [])
    return {
        "symbol": symbol,
        "trades": trades,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.post("/execute_trade/{symbol}")
async def execute_trade(symbol: str, db: Session = Depends(get_db)) -> Dict[str, str]:
    await bot._execute_trade(symbol, db)
    return {"message": f"✅ Trade ejecutado para {symbol}", "timestamp": datetime.now(timezone.utc).isoformat()}



@app.get("/config")
async def get_config() -> Dict[str, Union[str, float, int, bool]]:
    return {
        "symbol": config.SYMBOL,
        "timeframe": config.TIMEFRAME,
        "order_type": config.ORDER_TYPE,
        "time_in_force": config.TIME_IN_FORCE,
        "orderbook_depth": config.ORDERBOOK_DEPTH,
        "bybit_env": config.BYBIT_ENV,  # Actualizamos esta clave
        "capital_usdt": config.CAPITAL_USDT,
        "risk_factor": config.RISK_FACTOR,
        "min_trade_size": config.MIN_TRADE_SIZE,
        "max_trade_size": config.MAX_TRADE_SIZE,
        "fee_rate": config.FEE_RATE,
        "tp_percentage": config.TP_PERCENTAGE,
        "sl_percentage": config.SL_PERCENTAGE,
        "egm_buy_threshold": config.EGM_BUY_THRESHOLD,
        "egm_sell_threshold": config.EGM_SELL_THRESHOLD,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.post("/config/update_all")
async def update_all_config(config_data: Dict[str, Union[str, float, int]]) -> Dict[str, str]:
    if "capital_usdt" in config_data:
        config.CAPITAL_USDT = float(config_data["capital_usdt"]) if float(config_data["capital_usdt"]) > 0 else config.CAPITAL_USDT
    if "risk_factor" in config_data:
        config.RISK_FACTOR = max(0.0, min(1.0, float(config_data["risk_factor"])))
    if "egm_buy_threshold" in config_data:
        config.EGM_BUY_THRESHOLD = float(config_data["egm_buy_threshold"])
    if "egm_sell_threshold" in config_data:
        config.EGM_SELL_THRESHOLD = float(config_data["egm_sell_threshold"])
    logger.info(f"✅ Configuración actualizada: {config_data}")
    return {"message": "Configuración actualizada", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.post("/start")
async def start_bot() -> Dict[str, str]:
    if not bot.running:
        bot.running = True
        asyncio.create_task(bot.start_async())
        return {"message": "✅ Bot iniciado", "timestamp": datetime.now(timezone.utc).isoformat()}
    return {"message": "⚠️ Bot ya está corriendo", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.post("/stop")
async def stop_bot() -> Dict[str, str]:
    if bot.running:
        bot.stop()
        return {"message": "🛑 Bot detenido", "timestamp": datetime.now(timezone.utc).isoformat()}
    return {"message": "⚠️ Bot ya está detenido", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/status")
async def get_status() -> Dict[str, Union[bool, int, List[str], str]]:
    return {
        "running": bot.running,
        "iterations": bot.iterations,
        "symbols": bot.symbols,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/check_reset")
async def check_reset(db: Session = Depends(get_db)):
    results_file = os.path.join(os.path.dirname(__file__), '..', 'logs', 'results.json')
    try:
        with open(results_file, "r", encoding="utf-8") as f:
            results_data = json.load(f)
    except FileNotFoundError:
        results_data = {"metadata": {"total_trades": 0}, "summary": {"total_profit": 0.0}}

    trades = db.query(Trade).all()
    results_reset = results_data["metadata"]["total_trades"] == 0 and results_data["summary"]["total_profit"] == 0.0
    trades_reset = len(trades) == 0
    return {"results_reset": results_reset, "trades_reset": trades_reset}

@app.get("/health")
async def health_check() -> Dict[str, str]:
    return {"status": "healthy" if bot.running else "unhealthy", "timestamp": datetime.now(timezone.utc).isoformat()}

server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8081))

async def main(reset: bool = False):
    try:
        if reset:
            bot.reset_trades()
        logger.info("🚀 Iniciando bot y servidor API...")
        await asyncio.gather(bot.start_async(), server.serve())
    except Exception as e:
        logger.error(f"❌ Error crítico en main(): {e}")
        await server.shutdown()
    except KeyboardInterrupt:
        logger.info("🛑 Interrupción del usuario detectada.")
        await server.shutdown()
        bot.stop()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Nertz Trading Engine")
    parser.add_argument("--reset", action="store_true", help="Reset all trades in DB and logs on startup")
    args = parser.parse_args()
    asyncio.run(main(reset=args.reset))
