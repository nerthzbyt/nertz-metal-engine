# Qwen Cloud Integration Guide

> **NertzMetalEngine (Nertzh)** — Alibaba Cloud Deployment & Qwen Cloud Architecture Reference
>
> Global AI Hackathon Series with Qwen Cloud | Track 4: Autopilot Agent

---

## Table of Contents

- [Overview](#overview)
- [Alibaba Cloud Services Used](#alibaba-cloud-services-used)
- [Architecture](#architecture)
- [Qwen Cloud: Signal Generator Agent](#qwen-cloud-signal-generator-agent)
- [Qwen Cloud: Memory Agent](#qwen-cloud-memory-agent)
- [Qwen Cloud: Backtesting Engine](#qwen-cloud-backtesting-engine)
- [Qwen Cloud: Strategy Optimization](#qwen-cloud-strategy-optimization)
- [Deployment on Alibaba Cloud ECS](#deployment-on-alibaba-cloud-ecs)
- [API & Data Flow](#api--data-flow)
- [Configuration](#configuration)
- [Latency & Performance Considerations](#latency--performance-considerations)
- [Proof of Deployment](#proof-of-deployment)

---

## Overview

NertzMetalEngine is an AI-powered autopilot trading agent that uses **Alibaba Cloud's Qwen models** as its intelligence layer. The system streams real-time crypto market data from Bybit V5, processes it through a multi-factor signal model, and leverages Qwen Cloud to generate, validate, and optimize trading decisions autonomously.

Alibaba Cloud serves three critical roles in this system:

1. **AI Brain** — Qwen models power the Signal Generator Agent and Memory Agent
2. **Compute Infrastructure** — ECS instances host the engine, API server, and WebSocket consumer
3. **Storage & Persistence** — SQLite on ECS + OSS for logs and backtesting artifacts

---

## Alibaba Cloud Services Used

| Service | Purpose | Integration Point |
|---------|---------|-------------------|
| **Qwen Cloud (Model Studio / DashScope)** | AI inference for signal generation, memory, and strategy tuning | `scripts/nertz.py` — signal validation and agent reasoning |
| **Alibaba Cloud ECS** | Backend compute for the trading engine and FastAPI server | Deployment target for `scripts/nertz.py` on port 8081 |
| **Alibaba Cloud OSS** | Object storage for trade logs, session results, and backtesting data | `scripts/utils.py` — `save_results()` output |
| **Alibaba Cloud VPC** | Network isolation and security for the backend instance | ECS networking configuration |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                      ALIBABA CLOUD (Qwen Cloud)                      │
│                                                                       │
│  ┌────────────────────────┐    ┌──────────────────────────────────┐  │
│  │   Signal Generator     │    │         Memory Agent             │  │
│  │   Agent (Qwen Model)   │    │                                  │  │
│  │                        │    │  - Trade history context         │  │
│  │  - Validates multi-    │    │  - Performance pattern tracking  │  │
│  │    factor signals      │    │  - Adaptive threshold memory     │  │
│  │  - EGM, ILD, ROL,     │    │  - Win/loss ratio analysis       │  │
│  │    PIO, OGM analysis   │    │                                  │  │
│  │  - Directional bias    │    │  Queried via Qwen Cloud API      │  │
│  └───────────┬────────────┘    └───────────────┬──────────────────┘  │
│              │                                  │                     │
│  ┌───────────┴──────────────────────────────────┴──────────────────┐ │
│  │              Backtesting Engine (Qwen Cloud Compute)             │ │
│  │                                                                  │ │
│  │  - Historical kline data processing                              │ │
│  │  - Feature weight refinement (0.2 EGM, 0.3 ILD, 0.3 ROL...)   │ │
│  │  - EMA crossover parameter tuning                                │ │
│  │  - Risk threshold calibration                                    │ │
│  └───────────────────────────────┬──────────────────────────────────┘ │
└──────────────────────────────────┼────────────────────────────────────┘
                                   │
                                   │  Qwen Cloud API (DashScope)
                                   │
┌──────────────────────────────────┼────────────────────────────────────┐
│                   ALIBABA CLOUD ECS (Backend Instance)                 │
│                                  │                                     │
│  ┌───────────────────────────────┴────────────────────────────────┐   │
│  │                    FastAPI Server (port 8081)                    │   │
│  │                                                                  │   │
│  │  GET  /status     GET  /metrics/{symbol}   POST /start          │   │
│  │  GET  /profit     GET  /orderbook/{symbol}  POST /stop          │   │
│  │  GET  /config     GET  /candles/{symbol}/{limit}                │   │
│  │  GET  /health     GET  /trades/{symbol}    POST /execute_trade  │   │
│  │  POST /config/update_thresholds            POST /config/update  │   │
│  └───────────────────────────────┬────────────────────────────────┘   │
│                                  │                                     │
│  ┌───────────────────────────────┴────────────────────────────────┐   │
│  │                 NertzMetalEngine (Core Agent)                    │   │
│  │                                                                  │   │
│  │  ┌──────────────┐    ┌────────────────┐    ┌─────────────────┐ │   │
│  │  │  WebSocket    │    │    Strategy    │    │   Execution     │ │   │
│  │  │  Consumer     │────│    Engine      │────│   Engine        │ │   │
│  │  │              │    │                │    │                 │ │   │
│  │  │  - klines    │    │  - Metrics     │    │  - Hard Lock    │ │   │
│  │  │  - orderbook │    │  - Triple EMA  │    │  - TP/SL calc   │ │   │
│  │  │  - tickers   │    │  - TP/SL       │    │  - Order submit │ │   │
│  │  └──────┬───────┘    └────────────────┘    └────────┬────────┘ │   │
│  │         │                                           │          │   │
│  │  ┌──────┴───────┐                            ┌──────┴────────┐ │   │
│  │  │  Bybit V5    │                            │   Bybit V5    │ │   │
│  │  │  WebSocket   │                            │   REST API    │ │   │
│  │  │  Stream      │                            │   (pybit)     │ │   │
│  │  └──────────────┘                            └───────────────┘ │   │
│  │                                                                  │   │
│  │  ┌──────────────────────────────────────────────────────────┐   │   │
│  │  │              SQLite Database (trading.db)                  │   │   │
│  │  │                                                            │   │   │
│  │  │  market_data  │  orderbook  │  trades  │  market_ticker   │   │   │
│  │  └──────────────────────────────────────────────────────────┘   │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                     Alibaba Cloud OSS                              │   │
│  │  logs/results_{timestamp}.json  │  logs/results.json              │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Qwen Cloud: Signal Generator Agent

The Signal Generator Agent is the primary decision-making component powered by Qwen Cloud. It receives the multi-factor market metrics computed locally and uses Qwen model inference to validate and refine the directional signal.

### Input Metrics

The engine computes five proprietary metrics from real-time market data (`scripts/utils.py:calculate_metrics`):

| Metric | Formula | Weight |
|--------|---------|--------|
| **EGM** (Exhaustion Gap Metric) | `clip((last_price - avg_price) / price_range, -1, 1)` | 0.2 |
| **ILD** (Imbalance Level Delta) | `clip((bid_vol - ask_vol) / total_vol, -1, 1)` | 0.3 |
| **ROL** (Relative Orderbook Liquidity) | `clip((bid_value - ask_value) / total_value, -1, 1)` | 0.3 |
| **PIO** (Price Impulse Oscillator) | `clip((vol[0] - avg_vol) / avg_vol, -1, 1)` | 0.1 |
| **OGM** (Orderbook Gap Metric) | `1 - clip(spread / 0.02, 0, 1)` | 0.1 |

Combined signal:
```
combined = clip((0.2*EGM + 0.3*ILD + 0.3*ROL + 0.1*PIO + 0.1*OGM) * 10, -10, 10)
```

### Qwen Cloud Role

1. **Signal Validation** — The Qwen model evaluates the combined metric vector against historical patterns to confirm or reject the raw signal
2. **Contextual Enrichment** — Qwen considers broader market context (volatility regime, trend strength) that the local metrics alone cannot capture
3. **Threshold Calibration** — Feature weights (0.2, 0.3, 0.3, 0.1, 0.1) were originally derived and continue to be refined through Qwen Cloud backtesting

### Decision Flow

```
Raw Market Data (Bybit WS)
        │
        ▼
  Local Metric Computation (utils.py)
        │
        ▼
  Qwen Cloud Signal Generator Agent
        │
        ├── BUY  (egm >= 0.5 OR combined >= 1.0)
        ├── SELL (egm <= -0.5 OR combined <= -1.0)
        └── HOLD (insufficient conviction)
        │
        ▼
  Hard Lock Execution Engine
```

---

## Qwen Cloud: Memory Agent

The Memory Agent uses Qwen Cloud to maintain a persistent, queryable record of the agent's trading history and performance. This enables the autopilot to adapt its behavior based on past outcomes.

### Memory Context

| Data Point | Source | Usage |
|------------|--------|-------|
| Trade history (entry/exit, P&L, metrics at time of trade) | `trades` table in SQLite | Pattern recognition for similar market conditions |
| Win rate by symbol and time-of-day | Aggregated from trade logs | Adaptive threshold adjustment |
| Consecutive loss tracking | `scripts/nertz.py:_execute_trade` | Risk scaling and cooldown triggers |
| Feature importance drift | Backtesting results on Qwen Cloud | Weight rebalancing signals |

### How It Works

1. After each trade, the engine persists the full trade record including all metric values at entry time (`scripts/nertz.py:Trade` model)
2. Trade results are saved to JSON files in the `logs/` directory via `utils.save_results()`
3. Qwen Cloud processes these records to identify patterns — e.g., "ILD > 0.6 with EGM < 0 in high-volatility regimes yields 68% win rate"
4. The Memory Agent feeds these insights back into the Signal Generator's validation step

---

## Qwen Cloud: Backtesting Engine

Backtesting is performed on Alibaba Cloud compute infrastructure using Qwen models to process and analyze large volumes of historical market data.

### Process

1. **Data Collection** — Historical kline data is fetched from Bybit V5 REST API (`scripts/nertz.py:fetch_initial_data`)
2. **Metric Replay** — The full multi-factor signal model is replayed across historical candles
3. **Qwen Analysis** — Qwen models evaluate strategy performance across different market regimes (trending, ranging, volatile)
4. **Weight Optimization** — Feature weights and thresholds are adjusted based on Qwen's analysis of what worked best in each regime
5. **Validation** — Optimized parameters are validated against an out-of-sample data split

### Output

Backtesting produces:
- Optimized feature weights for the combined signal formula
- Recommended TP/SL percentage ranges per volatility regime
- EGM buy/sell threshold recommendations
- Performance reports saved to `logs/results_{timestamp}.json`

---

## Qwen Cloud: Strategy Optimization

Qwen Cloud assists in continuous strategy refinement beyond initial backtesting:

- **EMA Crossover Tuning** — Short (5), mid (10), and long (20) window parameters are validated against historical data
- **Risk Parameter Calibration** — `RISK_FACTOR`, `TP_PERCENTAGE`, and `SL_PERCENTAGE` are tuned based on observed volatility distributions
- **Cooldown Optimization** — `DEFAULT_SLEEP_TIME` (60s) is calibrated to balance trade frequency against signal quality

---

## Deployment on Alibaba Cloud ECS

### Instance Configuration

| Parameter | Value |
|-----------|-------|
| **Instance Type** | Alibaba Cloud ECS (compute-optimized) |
| **OS** | Ubuntu 22.04 LTS |
| **Python** | 3.11+ |
| **Runtime** | asyncio event loop with uvicorn ASGI server |
| **Port** | 8081 (FastAPI REST API) |
| **Database** | SQLite (local on ECS volume) |

### Deployment Steps

```bash
# 1. Clone the repository on the ECS instance
git clone https://github.com/nerthzbyt/nertz-metal-engine.git
cd nertz-metal-engine

# 2. Set up Python environment
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env with Bybit API keys and Alibaba Cloud credentials

# 4. Run the engine
python -m scripts.nertz
```

### Process Management

The engine runs as a long-lived asyncio process managed by the system. The FastAPI server starts concurrently with the WebSocket consumer:

```python
# scripts/nertz.py (main entry point)
async def main():
    await asyncio.gather(bot.start_async(), server.serve())

# uvicorn server configuration
server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8081))
```

---

## API & Data Flow

### Real-Time Data Pipeline

```
Bybit V5 WebSocket ──► WebSocket Consumer ──► Metric Computation ──► Qwen Cloud ──► Execution Engine
     (klines)              (nertz.py)            (utils.py)         (Signal Agent)    (Hard Lock)
     (orderbook)                                                                         │
     (tickers)                                                                           ▼
                                                                                    Bybit V5 REST
                                                                                    (Order Placement)
```

### Data Storage Flow

```
Market Data ──► SQLite (market_data table)
Orderbook   ──► SQLite (orderbook table)
Tickers     ──► SQLite (market_ticker table)
Trades      ──► SQLite (trades table)
Results     ──► JSON files (logs/results_{timestamp}.json) ──► Alibaba Cloud OSS
```

---

## Configuration

All configuration is managed via environment variables in `.env`:

### Trading Parameters

| Variable | Default | Description |
|----------|---------|-------------|
| `BYBIT_API_KEY` | — | Bybit API key for order execution |
| `BYBIT_API_SECRET` | — | Bybit API secret |
| `BYBIT_ENV` | `demo` | `demo` or `live` environment |
| `SYMBOL` | `BTCUSDT` | Trading pair(s), comma-separated |
| `TIMEFRAME` | `1m` | Kline interval |
| `ORDER_TYPE` | `limit` | `limit` or `market` |
| `ORDERBOOK_DEPTH` | `5` | Orderbook levels to track |

### Risk Management

| Variable | Default | Description |
|----------|---------|-------------|
| `CAPITAL_USDT` | `5000.0` | Starting capital |
| `RISK_FACTOR` | `0.01` | Risk per trade (fraction of capital) |
| `TP_PERCENTAGE` | `0.02` | Take profit target |
| `SL_PERCENTAGE` | `0.01` | Stop loss threshold |
| `MIN_TRADE_SIZE` | `0.001` | Minimum order quantity |
| `MAX_TRADE_SIZE` | `100.0` | Maximum order quantity |
| `FEE_RATE` | `0.002` | Exchange fee rate |

### Signal Thresholds

| Variable | Default | Description |
|----------|---------|-------------|
| `EGM_BUY_THRESHOLD` | `0.5` | EGM value to trigger buy |
| `EGM_SELL_THRESHOLD` | `-0.5` | EGM value to trigger sell |
| `PIO_THRESHOLD` | `0.1` | Price impulse oscillator threshold |
| `DEFAULT_SLEEP_TIME` | `60` | Cooldown between trades (seconds) |

---

## Latency & Performance Considerations

### Why Alibaba Cloud for Crypto Trading

| Factor | Benefit |
|--------|---------|
| **Geographic proximity** | Alibaba Cloud's Asia-Pacific data centers minimize network latency to Bybit's matching engines |
| **Qwen inference speed** | DashScope API delivers model inference within the 1-minute kline interval window |
| **Compute burst capacity** | ECS instances scale to handle backtesting workloads without impacting real-time trading |
| **Network reliability** | Alibaba Cloud's backbone provides stable WebSocket connections for continuous market data streaming |

### Latency Budget

| Component | Target Latency |
|-----------|---------------|
| Bybit WebSocket to ECS | < 50ms |
| Metric computation (local) | < 5ms |
| Qwen Cloud inference | < 2s |
| Order placement (ECS to Bybit REST) | < 100ms |
| **Total decision-to-execution** | **< 3s** |

The 3-second total latency is well within the 60-second kline interval, ensuring signals are generated and executed before the next candle closes.

---

## Proof of Deployment

This document serves as proof of Alibaba Cloud deployment for the NertzMetalEngine project.

### Code Evidence

| File | Alibaba Cloud Integration |
|------|--------------------------|
| `scripts/nertz.py` | Main engine deployed on Alibaba Cloud ECS; FastAPI server on port 8081 |
| `scripts/intelligence.py` | DashScope OpenAI-compatible API (`qwen-plus`) for signal validation |
| `scripts/qwen_agent.py` | Qwen Cloud Signal Generator Agent implementation |
| `scripts/memory_agent.py` | Trade memory context fed into Qwen prompts |
| `monitor_agent.py` | Production observability agent polling ECS-hosted engine |
| `scripts/utils.py` | Metric computation feeding Qwen Cloud Signal Generator Agent |
| `scripts/settings.py` | Configuration management for cloud-deployed instance |
| `docs/architecture-diagram.md` | Visual architecture for hackathon judges |
| `README.md` | Architecture documentation showing Alibaba Cloud integration |
| `docs/qwen-integration.md` | This document — full integration reference |

### Runtime Verification on ECS

```bash
# 1. Start engine (bot + API)
python -m scripts.nertz

# 2. Verify Qwen + Hard Lock state
curl http://localhost:8081/health
curl http://localhost:8081/intelligence/status

# 3. Start monitor agent (optional second agent)
python monitor_agent.py
curl http://localhost:8090/monitor/health
```

Record a short video showing the ECS terminal + `curl /health` with `qwen_configured: true` for the hackathon submission form.

### Repository

- **Source**: https://github.com/nerthzbyt/nertz-metal-engine
- **Integration doc**: https://github.com/nerthzbyt/nertz-metal-engine/blob/main/docs/qwen-integration.md

### Hackathon

- **Event**: Global AI Hackathon Series with Qwen Cloud
- **Track**: Track 4 — Autopilot Agent
- **Team**: NerT_dev

---

*Last updated: 2026-06-22*
