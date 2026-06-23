# Devpost Submission — NertzMetalEngine

**Profile:** https://devpost.com/nerthzbyt  
**Project URL:** https://devpost.com/software/v1_ (rename to NertzMetalEngine)  
**Hackathon:** https://qwencloud-hackathon.devpost.com/  
**Track:** Track 4 — Autopilot Agent  
**Repo:** https://github.com/nerthzbyt/nertz-metal-engine  
**Team:** NerT_dev

---

## Tagline (fix portfolio card)

**NertzMetalEngine — AI autopilot trading agent with Qwen Cloud validation and Structural Hard Lock execution discipline.**

---

## Short Description

Nertzh is a production-oriented autopilot agent that automates crypto trading workflows end-to-end: ingest live market data, compute multi-factor signals, validate decisions with **Qwen Cloud (DashScope)**, execute on **Bybit V5**, and enforce exit discipline via **Structural Hard Lock**. A second **Monitor Agent** provides human-in-the-loop checkpoints through FastAPI (`/execute_trade`, `/stop`, `/config`).

---

## Inspiration

Financial markets are noisy. Most algorithmic systems are too reactive — AI generates valid opposite signals while positions are still open, destroying Risk/Reward edge. We built NertzMetalEngine so Qwen Cloud provides intelligent validation while Hard Lock enforces mathematical discipline.

---

## What it does

- Streams Bybit V5 WebSocket data (klines, orderbook, tickers)
- Computes proprietary metrics: **EGM, ILD, ROL, PIO, OGM**
- **IntelligenceLayer**: XGBoost direction + memory context + **Qwen Cloud** confirmation
- **Hard Lock**: blocks new signals while a position is open; persists to `active_positions`
- FastAPI control plane on port **8081** (start/stop, manual trade, config)
- Monitor agent on port **8090** (health, alerts, SQLite audit)
- SQLite persistence + append-only `logs/results.json` event stream

---

## How we built it

- Python 3.11+ / asyncio / FastAPI / SQLAlchemy / NumPy
- **Alibaba Cloud Qwen** via DashScope OpenAI-compatible API (`qwen-plus`)
- **Alibaba Cloud ECS** backend deployment
- Bybit V5 (`pybit`) REST + WebSocket
- Modular metrics: `formulas.py`, `parameters.py`, `intelligence.py`

---

## Challenges

Engineering Hard Lock required redesigning the state machine so market data continues flowing for Qwen analysis while execution ignores opposing signals until TP/SL resolves. Integrating Qwen inference within the 1-minute kline window required async pipeline design and fallback mode when API keys are absent.

---

## What we learned

Trade management beats entry prediction. ~56% directional accuracy is useless without exit discipline. AI agents need guardrails — Qwen validates, Hard Lock executes.

---

## Built with

- python, numpy, fastapi, sqlalchemy, pybit, websockets
- **qwen** (Alibaba Cloud DashScope)
- **bybit**, asyncio, xgboost, scikit-learn

---

## Submission checklist

| Field | Value |
|-------|-------|
| GitHub repo | https://github.com/nerthzbyt/nertz-metal-engine |
| License | MIT (visible in repo About) |
| Track | Track 4: Autopilot Agent |
| Architecture diagram | `docs/architecture-diagram.md` |
| Alibaba proof | `docs/qwen-integration.md` + ECS screen recording |
| Demo video | YouTube 3 min (pending) |
| Try it | `python -m scripts.nertz` then `GET http://localhost:8081/health` |

---

## Qwen Cloud credits (coupon)

1. Register hackathon: https://qwencloud-hackathon.devpost.com/register
2. Apply voucher: https://www.qwencloud.com/challenge/hackathon/voucher-application
3. Join Discord: https://discord.gg/cDEHSV4Qqj
4. Set `DASHSCOPE_API_KEY` in `.env`