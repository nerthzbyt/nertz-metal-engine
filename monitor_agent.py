#!/usr/bin/env python3
"""
NertzMetalEngine Monitor Agent
================================
Second autopilot agent: watches the trading engine health, parses results.json
metric events, and exposes observability endpoints for hackathon judges.

Run:
  .venv\\Scripts\\python.exe monitor_agent.py
  .venv\\Scripts\\python.exe monitor_agent.py --port 8090
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from sqlalchemy import text

PROJECT_DIR = Path(__file__).parent
LOGS_DIR = PROJECT_DIR / "logs"
DATA_DIR = PROJECT_DIR / "data"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(PROJECT_DIR / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("NertzMonitor")


@dataclass
class MonitorConfig:
    engine_api: str = "http://localhost:8081"
    monitor_port: int = 8090
    symbols: List[str] = field(default_factory=lambda: ["BTCUSDT"])
    health_interval: int = 30
    results_path: Path = field(default_factory=lambda: LOGS_DIR / "results.json")

    @classmethod
    def from_env(cls) -> "MonitorConfig":
        cfg = cls()
        cfg.engine_api = os.getenv("NERTZH_API", "http://localhost:8081").rstrip("/")
        cfg.monitor_port = int(os.getenv("MONITOR_PORT", "8090"))
        raw = os.getenv("SYMBOL", "BTCUSDT")
        cfg.symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
        return cfg


class EngineCollector:
    def __init__(self, base_url: str, symbols: List[str]):
        self.base_url = base_url
        self.symbols = symbols

    async def poll(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "engine_reachable": False,
            "health": {},
            "status": {},
            "profit": {},
            "intelligence": {},
            "metrics": {},
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                health = await client.get(f"{self.base_url}/health")
                out["health"] = health.json()
                out["engine_reachable"] = health.status_code == 200
            except Exception as exc:
                out["health"] = {"error": str(exc)}

            if not out["engine_reachable"]:
                return out

            for path, key in (
                ("/status", "status"),
                ("/profit", "profit"),
                ("/intelligence/status", "intelligence"),
            ):
                try:
                    resp = await client.get(f"{self.base_url}{path}")
                    if resp.status_code == 200:
                        out[key] = resp.json()
                except Exception as exc:
                    out[key] = {"error": str(exc)}

            metrics_by_symbol: Dict[str, Any] = {}
            for symbol in self.symbols:
                try:
                    resp = await client.get(f"{self.base_url}/metrics/{symbol}")
                    if resp.status_code == 200:
                        metrics_by_symbol[symbol] = resp.json()
                except Exception as exc:
                    metrics_by_symbol[symbol] = {"error": str(exc)}
            out["metrics"] = metrics_by_symbol
        return out


class ResultsCollector:
    def __init__(self, path: Path):
        self.path = path

    def load_events(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            with open(self.path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return []
        if isinstance(payload, list):
            return [e for e in payload if isinstance(e, dict)]
        if isinstance(payload, dict):
            events = payload.get("events")
            if isinstance(events, list):
                return [e for e in events if isinstance(e, dict)]
        return []

    def summarize(self) -> Dict[str, Any]:
        events = self.load_events()
        metric_events = [e for e in events if e.get("type") == "metrics"]
        trade_events = [e for e in events if e.get("type") == "trade" or "profit_loss" in e]
        latest_metric = metric_events[-1] if metric_events else {}
        return {
            "results_file": str(self.path),
            "exists": self.path.exists(),
            "event_count": len(events),
            "metric_snapshots": len(metric_events),
            "trade_events": len(trade_events),
            "latest_metric": latest_metric,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


class SQLiteCollector:
    def check(self) -> Dict[str, Any]:
        try:
            from scripts.models import SessionLocal, engine

            with SessionLocal() as db:
                tables = db.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
                counts = {}
                for (name,) in tables:
                    if name.startswith("sqlite_"):
                        continue
                    row = db.execute(text(f"SELECT COUNT(*) FROM {name}")).fetchone()
                    counts[name] = int(row[0]) if row else 0
            return {
                "status": "ok",
                "database": str(engine.url),
                "tables": counts,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:
            return {"status": "error", "error": str(exc)}


class MonitoringAgent:
    def __init__(self, config: MonitorConfig):
        self.config = config
        self.engine = EngineCollector(config.engine_api, config.symbols)
        self.results = ResultsCollector(config.results_path)
        self.sqlite = SQLiteCollector()
        self.last_snapshot: Dict[str, Any] = {}
        self.alerts: List[Dict[str, Any]] = []

    async def run_check(self) -> Dict[str, Any]:
        engine_data = await self.engine.poll()
        results_data = self.results.summarize()
        sqlite_data = self.sqlite.check()

        alerts: List[Dict[str, Any]] = []
        if not engine_data.get("engine_reachable"):
            alerts.append({"level": "critical", "message": "Trading engine unreachable"})
        elif not engine_data.get("health", {}).get("running"):
            alerts.append({"level": "warning", "message": "Trading engine stopped"})
        if not os.getenv("DASHSCOPE_API_KEY", "").strip():
            alerts.append({"level": "info", "message": "Qwen Cloud running in fallback mode (no DASHSCOPE_API_KEY)"})

        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "engine": engine_data,
            "results": results_data,
            "sqlite": sqlite_data,
            "alerts": alerts,
            "hackathon": {
                "event": "Global AI Hackathon Series with Qwen Cloud",
                "track": "Track 4 — Autopilot Agent",
                "team": "NerT_dev",
            },
        }
        self.last_snapshot = snapshot
        self.alerts = alerts
        return snapshot

    async def background_loop(self) -> None:
        while True:
            try:
                await self.run_check()
            except Exception as exc:
                logger.warning("Monitor check failed: %s", exc)
            await asyncio.sleep(self.config.health_interval)


def build_app(agent: MonitoringAgent) -> FastAPI:
    app = FastAPI(title="NertzMetalEngine Monitor", version="1.0.0")

    @app.on_event("startup")
    async def _startup() -> None:
        asyncio.create_task(agent.background_loop())

    @app.get("/monitor/health")
    async def monitor_health() -> Dict[str, Any]:
        return await agent.run_check()

    @app.get("/monitor/full-report")
    async def full_report() -> Dict[str, Any]:
        if agent.last_snapshot:
            return agent.last_snapshot
        return await agent.run_check()

    @app.get("/monitor/alerts")
    def alerts() -> Dict[str, Any]:
        return {
            "alerts": agent.alerts,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="NertzMetalEngine monitoring agent")
    parser.add_argument("--port", type=int, default=None, help="Monitor API port (default 8090)")
    args = parser.parse_args()

    config = MonitorConfig.from_env()
    if args.port:
        config.monitor_port = args.port

    agent = MonitoringAgent(config)
    app = build_app(agent)
    logger.info("Monitor agent listening on :%s (engine=%s)", config.monitor_port, config.engine_api)
    uvicorn.run(app, host="0.0.0.0", port=config.monitor_port, log_level="info")


if __name__ == "__main__":
    main()