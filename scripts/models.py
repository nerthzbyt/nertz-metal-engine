"""DB models extracted from nertz for cleaner structure and line reduction."""
import os

from sqlalchemy import JSON, Column, DateTime, Float, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

DATABASE_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(DATABASE_DIR, exist_ok=True)
DATABASE_PATH = os.path.join(DATABASE_DIR, "trading.db")
engine = create_engine(
    f"sqlite:///{DATABASE_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


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
    trade_id = Column(Integer, nullable=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    symbol = Column(String(10), nullable=False, index=True)
    action = Column(String, nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    profit_loss = Column(Float, nullable=False)
    decision = Column(String, nullable=True)
    combined = Column(Float, default=0.0)
    ild = Column(Float, default=0.0)
    egm = Column(Float, default=0.0)
    rol = Column(Float, default=0.0)
    pio = Column(Float, default=0.0)
    ogm = Column(Float, default=0.0)
    risk_reward_ratio = Column(Float, default=0.0)


class ActivePosition(Base):
    __tablename__ = "active_positions"
    symbol = Column(String(10), primary_key=True, index=True)
    timestamp = Column(DateTime, nullable=False)
    action = Column(String, nullable=False)
    entry_price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    tp = Column(Float, nullable=False)
    sl = Column(Float, nullable=False)
    combined = Column(Float, nullable=False, default=0.0)
    ild = Column(Float, default=0.0)
    egm = Column(Float, default=0.0)
    rol = Column(Float, default=0.0)
    pio = Column(Float, default=0.0)
    ogm = Column(Float, default=0.0)
    order_id = Column(String, nullable=True)
