# NertzMetalEngine (Nertzh)

**An AI-powered, disciplined automated trading engine for crypto markets built on Bybit V5 API and Qwen Cloud.**

---

## Inspiration

The financial markets are inherently noisy, and most algorithmic trading systems suffer from a critical flaw: **they are too reactive**. While AI and ML models can predict directional movements with high accuracy, transient market noise often triggers premature exits, destroying a strategy's statistical edge.

NertzMetalEngine (Nertzh) was built to solve this exact problem — an automated trading engine that doesn't just predict the market, but possesses the *mathematical discipline* to let its edge play out without panicking over temporary fluctuations.

## What It Does

Nertzh is a real-time, fully-automated crypto trading bot that:

- Streams live market data (orderbook, klines, tickers) via **Bybit V5 WebSocket API**
- Computes a **multi-factor entry signal** combining EGM, ILD, ROL, PIO, and OGM metrics
- Uses a **Triple EMA crossover** strategy for trend confirmation
- Executes trades with **dynamic TP/SL** based on real-time volatility
- Exposes a **FastAPI REST API** for monitoring, configuration, and manual trade execution
- Persists all market data and trade history in a **SQLite** database

## Key Innovation: Structural Hard Lock

The core innovation is the **Structural Hard Lock** mechanism in the execution engine:

1. **Signal Suppression** — Once a position is opened with predefined TP and SL, the engine blocks re-evaluation of opposing entry signals
2. **Exchange-Level Execution** — The bot relies on Bybit's native TP/SL triggers, removing local network latency from the exit equation
3. **Timeout & Hold Bands** — A configurable cooldown acts as a secondary fail-safe, ensuring trades have time to breathe

By decoupling the *signal generation engine* from the *position management engine*, the bot remains a disciplined executor rather than an overactive trader.

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  FastAPI Server                  │
│  /status /profit /metrics /config /trades /health│
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────┴──────────────────────────┐
│            NertzMetalEngine (Core)               │
│  ┌────────────┐  ┌────────────┐  ┌───────────┐  │
│  │  WebSocket  │  │  Strategy  │  │ Execution │  │
│  │  Consumer   │──│  Engine    │──│  Engine   │  │
│  └─────┬──────┘  └────────────┘  └─────┬─────┘  │
│        │                               │        │
│  ┌─────┴──────┐                  ┌─────┴─────┐  │
│  │  Bybit V5  │                  │  Bybit V5 │  │
│  │  WS Stream │                  │  REST API │  │
│  └────────────┘                  └───────────┘  │
│                                                  │
│  ┌────────────────────────────────────────────┐  │
│  │         SQLite Database (trading.db)        │  │
│  │  market_data | orderbook | trades | tickers │  │
│  └────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘
```

## Multi-Factor Signal Model

The entry signal is a weighted combination of five proprietary metrics:

| Metric | Name | Description | Weight |
|--------|------|-------------|--------|
| **EGM** | Exhaustion Gap Metric | Measures price deviation from moving average | 0.2 |
| **ILD** | Imbalance Level Delta | Bid/ask volume imbalance from orderbook | 0.3 |
| **ROL** | Relative Orderbook Liquidity | Bid/ask value-weighted imbalance | 0.3 |
| **PIO** | Price Impulse Oscillator | Volume spike detection | 0.1 |
| **OGM** | Orderbook Gap Metric | Spread tightness indicator | 0.1 |

Combined score formula:

```
combined = clip((0.2*EGM + 0.3*ILD + 0.3*ROL + 0.1*PIO + 0.1*OGM) * 10, -10, 10)
```

## How We Built It

- **Python 3.11+** with `asyncio` for high-throughput concurrent processing
- **Bybit V5 API** via `pybit` SDK for order execution and WebSocket for real-time data
- **FastAPI** for the monitoring and control REST API (port 8081)
- **SQLAlchemy + SQLite** for persistent market data and trade history storage
- **NumPy** for numerical computation of trading metrics
- **Qwen Cloud** compute infrastructure for backtesting and refining feature weights

## Project Structure

```
├── scripts/
│   ├── __init__.py
│   ├── nertz.py        # Main engine: WebSocket consumer, trade execution, FastAPI server
│   ├── settings.py     # Configuration management with validation
│   └── utils.py        # Metrics calculation, TP/SL logic, trading strategies
├── .env.example        # Environment variables template
├── requirements.txt    # Python dependencies
├── LICENSE             # MIT License
└── README.md           # This file
```

## Getting Started

### Prerequisites

- Python 3.11+
- A Bybit account with API keys ([create keys here](https://www.bybit.com/app/user/api-management))

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/nerthzbyt/nertz-metal-engine.git
   cd nertz-metal-engine
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Linux/macOS
   .venv\Scripts\activate     # Windows
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your Bybit API credentials
   ```

5. **Run the engine**
   ```bash
   python -m scripts.nertz
   ```

The engine starts the WebSocket consumer and the FastAPI server on port **8081**.

### Configuration

All settings are managed via environment variables in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `BYBIT_API_KEY` | — | Your Bybit API key |
| `BYBIT_API_SECRET` | — | Your Bybit API secret |
| `BYBIT_ENV` | `demo` | Environment: `demo` or `live` |
| `SYMBOL` | `BTCUSDT` | Trading pair(s), comma-separated |
| `TIMEFRAME` | `1m` | Candle interval: `1m`, `5m`, `15m`, `1h`, `4h`, `1d` |
| `CAPITAL_USDT` | `5000.0` | Starting capital in USDT |
| `RISK_FACTOR` | `0.01` | Risk per trade as fraction of capital |
| `TP_PERCENTAGE` | `0.02` | Take profit percentage |
| `SL_PERCENTAGE` | `0.01` | Stop loss percentage |
| `EGM_BUY_THRESHOLD` | `0.5` | EGM threshold for buy signals |
| `EGM_SELL_THRESHOLD` | `-0.5` | EGM threshold for sell signals |

## API Endpoints

The FastAPI server exposes the following endpoints:

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/status` | Bot running status and iteration count |
| `GET` | `/health` | Health check |
| `GET` | `/config` | Current configuration |
| `GET` | `/profit` | P&L summary with win rate and per-symbol breakdown |
| `GET` | `/metrics/{symbol}` | Live trading metrics (EGM, ILD, ROL, PIO, OGM) |
| `GET` | `/market_data/{symbol}` | Latest candle data |
| `GET` | `/ticker/{symbol}` | Current ticker information |
| `GET` | `/orderbook/{symbol}` | Live orderbook snapshot |
| `GET` | `/candles/{symbol}/{limit}` | Historical candles |
| `GET` | `/trades/{symbol}` | Trade history |
| `POST` | `/execute_trade/{symbol}` | Manually trigger trade evaluation |
| `POST` | `/start` | Start the bot |
| `POST` | `/stop` | Stop the bot |
| `POST` | `/config/update_thresholds` | Update EGM thresholds |
| `POST` | `/config/update_all` | Update all configuration |
| `GET` | `/check_reset` | Check if data has been reset |

## Challenges Faced

The biggest challenge was preventing the bot from being "too smart for its own good." Initially, the AI would generate a valid opposite signal while a position was still active, and the bot would prematurely close a winning or recovering trade. This destroyed the target Risk/Reward ratio:

```
R:R = Expected Profit / Expected Loss >= 3.0
```

Engineering the "Hard Lock" required redesigning the bot's state machine from the ground up — ensuring that while the *market data* stream continued to flow and log metrics for analysis, the *execution* thread was entirely blinded to new directional signals until the active trade resolved.

## What We Learned

The most crucial lesson: **trade management (exit logic) is far more critical than entry prediction**. A ~56% directional win rate is useless if the exit logic doesn't respect the Risk/Reward ratio. A bot that cuts winners early out of "fear" (reacting to opposite micro-signals) will never be profitable long-term.

## Built With

- **Python** — Core language
- **Bybit V5 API** — Exchange integration (REST + WebSocket)
- **FastAPI** — REST API server
- **SQLAlchemy** — ORM for trade and market data persistence
- **NumPy** — Numerical computation
- **asyncio** — Asynchronous I/O
- **WebSockets** — Real-time market data streaming
- **Qwen Cloud** — Compute infrastructure for backtesting

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## Disclaimer

This software is for educational and research purposes. Trading cryptocurrencies involves substantial risk of loss. Use at your own risk. The authors are not responsible for any financial losses incurred through the use of this software.
