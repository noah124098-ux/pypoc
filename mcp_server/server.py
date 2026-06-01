"""MCP server for the NSE Trading Agent.

Exposes read-only tools so any MCP client (Claude Code, Claude Desktop, etc.)
can inspect the live agent. Run via:

    python -m mcp_server.server

Or register in `.mcp.json` (see project root) so Claude Code launches it automatically.

Important: this server is READ-ONLY. It cannot place orders, halt the agent, or
modify state. Mutating control comes in a later phase via a command-queue file
that the orchestrator polls.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from mcp_server.tools import TradingAgentTools

log = logging.getLogger("mcp_server")
logging.basicConfig(level=os.getenv("MCP_LOG_LEVEL", "INFO"))


def _build_tool_schemas() -> list[Tool]:
    """JSON Schema for each tool we expose. Claude Code uses these to call us."""
    return [
        Tool(
            name="get_status",
            description=(
                "Get live status of the NSE trading agent: feed connectivity, equity, "
                "current regime, halt status. Returns 'running: false' if the agent isn't running."
            ),
            inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        Tool(
            name="get_positions",
            description="List currently open positions with stop-loss, target, and unrealized P&L.",
            inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        Tool(
            name="get_pnl",
            description="Realized + unrealized P&L summary, day P&L %, drawdown from peak %.",
            inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        Tool(
            name="get_recent_signals",
            description=(
                "Most recent strategy signals (accepted and rejected). Use accepted_only=true "
                "to filter to only orders that were placed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 500},
                    "accepted_only": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_recent_trades",
            description="Recently closed trades with entry/exit price, P&L, charges, exit reason.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 500}
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_guardrail_rejections",
            description=(
                "Recent guardrail rejections. Useful to see which safety rules are firing "
                "and why orders are being blocked."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 500}
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_equity_curve",
            description="Time series of equity snapshots. Useful for plotting agent performance.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 500, "minimum": 1, "maximum": 5000}
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_regime_history",
            description="History of regime classifications (TREND / RANGE / VOLATILE / UNKNOWN).",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 500}
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_universe",
            description="Configured trading universe (e.g. Nifty 50 symbol list).",
            inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        Tool(
            name="get_config_summary",
            description=(
                "Active risk and strategy configuration: per-trade risk %, daily loss circuit, "
                "drawdown circuit, regime thresholds, strategies enabled."
            ),
            inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        # --- Mutating tools (via command queue) ---
        Tool(
            name="halt_agent",
            description=(
                "Enqueue a halt_agent command. The orchestrator stops trading when it drains "
                "the queue. Provide an optional reason string."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "default": "manual halt via MCP",
                        "description": "Human-readable reason for the halt.",
                    }
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="resume_agent",
            description="Enqueue a resume_agent command to clear a manual halt.",
            inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        Tool(
            name="update_risk_param",
            description=(
                "Enqueue a risk-parameter update. Allowed params: per_trade_risk_pct [0.5-2.0], "
                "max_open_positions [2-8], daily_loss_circuit_pct [2.0-5.0]. "
                "Value is validated before queuing."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "param": {
                        "type": "string",
                        "enum": ["per_trade_risk_pct", "max_open_positions", "daily_loss_circuit_pct"],
                    },
                    "value": {"type": "number"},
                },
                "required": ["param", "value"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="place_paper_order",
            description=(
                "Enqueue a manual paper order for a Nifty 50 symbol. "
                "The orchestrator will run it through all guardrails before filling."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE symbol, e.g. RELIANCE"},
                    "side": {"type": "string", "enum": ["BUY", "SELL"]},
                    "qty": {"type": "integer", "minimum": 1, "maximum": 1000},
                    "strategy": {"type": "string", "default": "manual"},
                },
                "required": ["symbol", "side", "qty"],
                "additionalProperties": False,
            },
        ),
    ]


def _build_dispatch(tools: TradingAgentTools):
    """Map MCP tool name -> callable. Argument validation is done by MCP from inputSchema."""
    return {
        "get_status": lambda _: tools.get_status(),
        "get_positions": lambda _: tools.get_positions(),
        "get_pnl": lambda _: tools.get_pnl(),
        "get_recent_signals": lambda a: tools.get_recent_signals(
            limit=int(a.get("limit", 50)), accepted_only=bool(a.get("accepted_only", False))
        ),
        "get_recent_trades": lambda a: tools.get_recent_trades(limit=int(a.get("limit", 50))),
        "get_guardrail_rejections": lambda a: tools.get_guardrail_rejections(
            limit=int(a.get("limit", 50))
        ),
        "get_equity_curve": lambda a: tools.get_equity_curve(limit=int(a.get("limit", 500))),
        "get_regime_history": lambda a: tools.get_regime_history(limit=int(a.get("limit", 50))),
        "get_universe": lambda _: tools.get_universe(),
        "get_config_summary": lambda _: tools.get_config_summary(),
        # mutating tools
        "halt_agent": lambda a: tools.halt_agent(reason=a.get("reason", "manual halt via MCP")),
        "resume_agent": lambda _: tools.resume_agent(),
        "update_risk_param": lambda a: tools.update_risk_param(
            param=str(a["param"]), value=float(a["value"])
        ),
        "place_paper_order": lambda a: tools.place_paper_order(
            symbol=str(a["symbol"]),
            side=str(a["side"]),
            qty=int(a["qty"]),
            strategy=str(a.get("strategy", "manual")),
        ),
    }


async def main() -> None:
    snapshot_path = os.getenv("AGENT_SNAPSHOT_PATH", "data/snapshot.json")
    sqlite_path = os.getenv("AGENT_SQLITE_PATH", "data/agent.db")
    config_path = os.getenv("AGENT_CONFIG_PATH", "config/default.yaml")
    tools = TradingAgentTools(snapshot_path, sqlite_path, config_path)
    dispatch = _build_dispatch(tools)
    schemas = _build_tool_schemas()

    server: Server = Server("nse-trading-agent")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return schemas

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        args = arguments or {}
        try:
            handler = dispatch.get(name)
            if handler is None:
                payload = {"error": f"Unknown tool: {name}"}
            else:
                result = handler(args)
                payload = {"ok": True, "result": result}
        except FileNotFoundError as e:
            payload = {"error": "agent_not_running", "detail": str(e)}
        except Exception as e:
            log.exception("Tool %s failed", name)
            payload = {"error": "internal_error", "detail": str(e)}
        return [TextContent(type="text", text=json.dumps(payload, default=str, indent=2))]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
