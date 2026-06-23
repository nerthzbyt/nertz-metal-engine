# NertzMetalEngine — Architecture Diagram

> **Hackathon:** Global AI Hackathon Series with Qwen Cloud  
> **Track:** Track 4 — Autopilot Agent  
> **Team:** NerT_dev

## System Overview

```mermaid
flowchart TB
    subgraph AlibabaCloud["Alibaba Cloud"]
        subgraph QwenCloud["Qwen Cloud (DashScope API)"]
            SG["Signal Generator Agent<br/>qwen-plus"]
            MEM["Memory Context<br/>trade history synthesis"]
        end
        ECS["Alibaba Cloud ECS<br/>NertzMetalEngine :8081"]
        OSS["Alibaba Cloud OSS<br/>logs & backtest artifacts"]
    end

    subgraph Agents["Multi-Agent Layer"]
        IL["IntelligenceLayer<br/>XGB + Memory + Qwen"]
        MON["Monitor Agent :8090<br/>health + alerts + SQLite audit"]
    end

    subgraph Exchange["Bybit V5"]
        WS["WebSocket<br/>klines / orderbook / ticker"]
        REST["REST API<br/>spot orders"]
    end

    subgraph Data["Persistence"]
        SQL["SQLite trading.db<br/>market_data | trades | active_positions"]
        JSON["logs/results.json<br/>event stream"]
    end

    WS --> ECS
    ECS --> IL
    IL --> SG
    IL --> MEM
    SG --> IL
    IL -->|validated signal| ECS
    ECS -->|Hard Lock execution| REST
    ECS --> SQL
    ECS --> JSON
    ECS --> OSS
    MON -->|poll /health /metrics| ECS
    MON --> SQL
    MON --> JSON
```

## Autopilot Workflow (Track 4)

```mermaid
sequenceDiagram
    participant M as Market Data (Bybit WS)
    participant E as NertzMetalEngine
    participant F as formulas.py
    participant I as IntelligenceLayer
    participant Q as Qwen Cloud
    participant H as Hard Lock
    participant O as Operator (Human-in-the-loop)

    M->>E: kline + orderbook + ticker
    E->>F: calculate_metrics()
    F-->>E: EGM, ILD, ROL, PIO, OGM, combined
    E->>I: local buy/sell candidate
    I->>Q: validate_signal()
    Q-->>I: confirm / hold + confidence
    alt Qwen confirms
        E->>H: open position + persist active_positions
        H-->>E: block opposing signals
    else Qwen rejects
        E-->>E: hold (logged)
    end
    O->>E: POST /execute_trade or /stop
    E->>H: TP/SL monitor on ticker
    H->>E: close + realized P&L
```

## Proof of Alibaba Cloud Deployment

| Evidence | Location |
|----------|----------|
| DashScope API integration | `scripts/intelligence.py`, `scripts/qwen_agent.py` |
| ECS deployment guide | `docs/qwen-integration.md` |
| Architecture reference | `docs/architecture-diagram.md` (this file) |
| Monitor agent (production ops) | `monitor_agent.py` |
| Open-source repo | https://github.com/nerthzbyt/nertz-metal-engine |

Record a short screen capture showing:

1. ECS instance running `python -m scripts.nertz`
2. `GET /health` returning `qwen_configured: true`
3. DashScope console with API usage