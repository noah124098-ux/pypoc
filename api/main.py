from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import json
from pathlib import Path

from core.runtime_snapshot import read as read_snapshot
from mcp_server.tools import TradingAgentTools

app = FastAPI(title="pypoc Trading API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/snapshot")
def get_snapshot():
    snap = read_snapshot("data/snapshot.json")
    return snap or {"running": False}


@app.get("/api/positions")
def get_positions():
    return TradingAgentTools().get_positions()


@app.get("/api/equity")
def get_equity(limit: int = 200):
    return TradingAgentTools().get_equity_curve(limit=limit)


@app.get("/api/trades")
def get_trades(limit: int = 50):
    return TradingAgentTools().get_recent_trades(limit=limit)


@app.get("/api/signals")
def get_signals(limit: int = 50):
    return TradingAgentTools().get_recent_signals(limit=limit)


@app.get("/api/gate")
def get_gate():
    gate_path = Path("data/backtest_gate.json")
    if gate_path.exists():
        return json.loads(gate_path.read_text())
    return {"passed": False, "error": "no gate file"}


@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            snap = read_snapshot("data/snapshot.json") or {}
            await websocket.send_json(snap)
            await asyncio.sleep(1)  # push every 1 second
    except WebSocketDisconnect:
        pass
