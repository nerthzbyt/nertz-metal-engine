# NertzMetalEngine (Nertzh)

**An AI-powered autopilot trading agent for crypto markets, built on Alibaba Cloud (Qwen Cloud) and Bybit V5 API.**

Nertzh leverages **Alibaba Cloud's Qwen models** as the intelligence layer of an autonomous trading agent — combining real-time market analysis, AI-driven signal generation, and disciplined execution into a fully automated pipeline deployed on Alibaba Cloud infrastructure.

---

## Alibaba Cloud Integration

This project is built for the **Global AI Hackathon Series with Qwen Cloud** (Track 4: Autopilot Agent). Alibaba Cloud powers the core intelligence and infrastructure of the system:

### Qwen Cloud — AI Autopilot Brain

| Capability | How Qwen Cloud Is Used |
|---|---|
| **Signal Generator Agent** | Qwen models analyze multi-factor market metrics (EGM, ILD, ROL, PIO, OGM) to generate and validate directional trading signals, acting as the agent's decision-making brain |
| **Memory Agent** | Qwen Cloud maintains contextual memory of past trades, market conditions, and strategy performance — enabling the agent to learn and adapt over time |
| **Backtesting Engine** | Historical market data is processed on Qwen Cloud compute to backtest strategies, refine feature weights, and validate the multi-factor signal model before live deployment |
| **Strategy Optimization** | Qwen models assist in tuning EMA crossover parameters, TP/SL ratios, and risk thresholds based on historical performance analysis |

### Alibaba Cloud Infrastructure

| Service | Role |
|---|---|
| **Alibaba Cloud ECS** | Backend compute hosting the NertzMetalEngine, FastAPI server, and WebSocket consumer |
| **Alibaba Cloud Model Studio** | API access to Qwen models for signal generation and agent reasoning |
| **Alibaba Cloud OSS** | Storage for backtesting results, trade logs, and session data |

### Why Alibaba Cloud?

- **Low-latency AI inference**: Qwen Cloud's proximity to Asian crypto exchanges (Bybit) minimizes round-trip time for AI-driven decisions
- **Scalable compute**: Backtesting across months of historical kline data requires burst compute that Alibaba Cloud provides on demand
- **Integrated AI ecosystem**: Model Studio + DashScope API enables seamless integration of Qwen models into the Python asyncio pipeline without custom ML infrastructure

---

## Inspiration

Financial markets are inherently noisy, and most algorithmic trading systems suffer from a critical flaw: **they are too reactive**. While AI models can predict directional movements with high accuracy, transient market noise often triggers premature exits, destroying a strategy's statistical edge.

NertzMetalEngine was built to solve this — an **autopilot agent** that doesn't just predict the market, but possesses the *mathematical discipline* to let its edge play out. Qwen Cloud serves as the agent's reasoning layer, providing AI-validated signals while the Hard Lock mechanism enforces execution discipline.

## What It Does

Nertzh is a real-time, fully-automated crypto trading autopilot agent that:

- Streams live market data (orderbook, klines, tickers) via **Bybit V5 WebSocket API**
- Uses **Qwen Cloud AI** as the Signal Generator Agent to compute and validate multi-factor entry signals (EGM, ILD, ROL, PIO, OGM)
- Leverages a **Memory Agent** powered by Qwen Cloud to track trade history, performance patterns, and adaptive thresholds
- Applies a **Triple EMA crossover** strategy for trend confirmation
- Executes trades with **dynamic TP/SL** based on real-time volatility
- Runs a **Structural Hard Lock** state machine to prevent premature position exits
- Exposes a **FastAPI REST API** for monitoring, configuration, and manual trade execution
- Persists all market data and trade history in **SQLite** on Alibaba Cloud storage
- Performs **backtesting on Qwen Cloud** to continuously refine strategy weights

## Key Innovation: Autopilot Agent Architecture

The autopilot agent combines three layers of intelligence:

```
┌─────────────────────────────────────────────────────────────┐
│                    ALIBABA CLOUD (Qwen)                      │
│  ┌───────────────────┐  ┌──────────────────────────────┐    │
│  │  Signal Generator │  │        Memory Agent          │    │
│  │    Agent (Qwen)   │  │  Trade history, performance  │    │
│  │  AI-validated     │  │  patterns, adaptive weights  │    │
│  │  multi-factor     │  │  stored & queried via        │    │
│  │  signals          │  │  Qwen Cloud                  │    │
│  └────────┬──────────┘  └──────────────┬───────────────┘    │
│           │                            │                     │
│  ┌────────┴────────────────────────────┴───────────────┐    │
│  │          Backtesting Engine (Qwen Cloud Compute)     │    │
│  │  Historical data processing, weight optimization     │    │
│  └────────────────────────┬────────────────────────────┘    │
└───────────────────────────┼─────────────────────────────────┘
                            │
┌───────────────────────────┼─────────────────────────────────┐
│            ALIBABA CLOUD ECS (Backend)                       │
│  ┌────────────────────────┴─────────────────────────────┐   │
│  │              FastAPI Server (port 8081)               │   │
│  │  /status /profit /metrics /config /trades /health     │   │
│  └────────────────────────┬─────────────────────────────┘   │
│                           │                                  │
│  ┌────────────────────────┴─────────────────────────────┐   │
│  │            NertzMetalEngine (Core Agent)              │   │
│  │  ┌────────────┐  ┌────────────┐  ┌───────────────┐  │   │
│  │  │  WebSocket  │  │  Strategy  │  │  Execution    │  │   │
│  │  │  Consumer   │──│  Engine    │──│  Engine       │  │   │
│  │  └─────┬──────┘  └────────────┘  └─────┬─────────┘  │   │
│  │        │                               │             │   │
│  │  ┌─────┴──────┐                  ┌─────┴─────┐      │   │
│  │  │  Bybit V5  │                  │  Bybit V5 │      │   │
│  │  │  WS Stream │                  │  REST API │      │   │
│  │  └────────────┘                  └───────────┘      │   │
│  │                                                       │   │
│  │  ┌──────────────────────────────────────────────┐    │   │
│  │  │         SQLite Database (trading.db)          │    │   │
│  │  │  market_data | orderbook | trades | tickers   │    │   │
│  │  └──────────────────────────────────────────────┘    │   │
│  └───────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

### Structural Hard Lock

The core execution innovation is the **Structural Hard Lock** mechanism:

1. **Signal Suppression** — Once a position is opened with predefined TP and SL, the engine blocks re-evaluation of opposing entry signals
2. **Exchange-Level Execution** — The bot relies on Bybit's native TP/SL triggers, removing local network latency from the exit equation
3. **Timeout & Hold Bands** — A configurable cooldown acts as a secondary fail-safe, ensuring trades have time to breathe

By decoupling the *signal generation agent* (Qwen Cloud) from the *position management engine* (Hard Lock), the autopilot remains a disciplined executor rather than an overactive trader.

## Multi-Factor Signal Model

The entry signal is a weighted combination of five proprietary metrics, validated by Qwen Cloud's AI analysis:

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

Feature weights were refined through **backtesting on Qwen Cloud** using months of historical kline data from Bybit.

## How We Built It

- **Python 3.11+** with `asyncio` for high-throughput concurrent processing
- **Alibaba Cloud Qwen Models** via DashScope/Model Studio API for signal generation, memory agent, and strategy optimization
- **Alibaba Cloud ECS** for backend deployment (engine + API server)
- **Bybit V5 API** via `pybit` SDK for order execution and WebSocket for real-time data
- **FastAPI** for the monitoring and control REST API (port 8081)
- **SQLAlchemy + SQLite** for persistent market data and trade history storage on Alibaba Cloud
- **NumPy** for numerical computation of trading metrics
- **Qwen Cloud Compute** for large-scale backtesting and feature weight refinement

## Project Structure

```
├── scripts/
│   ├── __init__.py
│   ├── nertz.py        # Main engine: WebSocket consumer, trade execution, FastAPI server
│   ├── settings.py     # Configuration management with validation
│   └── utils.py        # Metrics calculation, TP/SL logic, trading strategies
├── data/               # SQLite database and session data
├── logs/               # Trade results and session logs
├── .env.example        # Environment variables template
├── requirements.txt    # Python dependencies
├── LICENSE             # MIT License
└── README.md           # This file
```

## Getting Started

### Prerequisites

- Python 3.11+
- A Bybit account with API keys ([create keys here](https://www.bybit.com/app/user/api-management))
- An **Alibaba Cloud** account with access to Qwen Cloud / Model Studio ([sign up here](https://www.alibabacloud.com))

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
   # Edit .env with your Bybit API credentials and Alibaba Cloud settings
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

Engineering the "Hard Lock" required redesigning the bot's state machine from the ground up — ensuring that while the *market data* stream continued to flow and log metrics for Qwen Cloud analysis, the *execution* thread was entirely blinded to new directional signals until the active trade resolved.

Integrating Qwen Cloud's AI inference into the real-time asyncio pipeline also required careful latency management — the signal generation agent needed to respond within the kline interval window to remain actionable.

## What We Learned

- **Trade management (exit logic) is far more critical than entry prediction**. A ~56% directional win rate is useless if the exit logic doesn't respect the Risk/Reward ratio.
- **AI agents need guardrails**: Qwen Cloud provides excellent signal quality, but the Hard Lock mechanism proves that even the best AI needs structural constraints to avoid overtrading.
- **Cloud-native AI integration works**: Running Qwen models via Alibaba Cloud's infrastructure eliminated the need for local GPU resources while keeping inference latency low enough for 1-minute kline strategies.

## Built With

- **Python** — Core language
- **Alibaba Cloud Qwen Models** — AI signal generation, memory agent, and strategy optimization
- **Alibaba Cloud ECS** — Backend deployment and compute
- **Alibaba Cloud Model Studio / DashScope** — Qwen model API access
- **Bybit V5 API** — Exchange integration (REST + WebSocket)
- **FastAPI** — REST API server
- **SQLAlchemy** — ORM for trade and market data persistence
- **NumPy** — Numerical computation
- **asyncio** — Asynchronous I/O
- **WebSockets** — Real-time market data streaming

## Deployment on Alibaba Cloud

The backend is deployed on **Alibaba Cloud ECS** instances with the following setup:

- **Compute**: Alibaba Cloud ECS instance running Python 3.11+
- **AI Services**: Qwen Cloud (Model Studio / DashScope API) for agent intelligence
- **Storage**: Local SQLite on ECS + Alibaba Cloud OSS for logs and backtesting data
- **Networking**: Public endpoint on port 8081 for API access, WebSocket connections to Bybit

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## Disclaimer

This software is for educational and research purposes. Trading cryptocurrencies involves substantial risk of loss. Use at your own risk. The authors are not responsible for any financial losses incurred through the use of this software.
