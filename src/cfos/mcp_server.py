"""Minimal MCP stdio server (JSON-RPC 2.0, newline-delimited).

No SDK dependency: the protocol surface used here is small and stable, and a
zero-dependency server starts faster and is easier to audit. Logs go to
stderr; stdout carries protocol messages only.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from . import __version__, doctor as doctor_mod
from .allocator import allocate, load_registry
from .config import Settings
from .execution import build_plan, close_position, execute, status, sync_paper
from .exchange import build_exchange, public_market_snapshot
from .guards import KillSwitch
from .journal import Journal
from .models import Side, dumps
from .paper import PaperExchange
from .router import route

INSTRUCTIONS = """\
CFOS is a crypto futures trading workspace. Rules that are not negotiable:

1. Call cfos_status before any trade. If kill_switch.engaged is true, do not place risk.
   Then call cfos_allocate: if it returns NO_TRADE, there is no measured edge in the current
   regime and no new risk may be opened.
2. Call cfos_size or cfos_plan; never invent a quantity. The risk kernel caps leverage,
   daily loss, weekly loss, drawdown, portfolio heat and correlation clusters.
3. cfos_execute refuses when a gate blocks. Do not retry by changing the numbers to slip
   past a gate; fix the setup or stand aside.
4. Live orders require CFOS_MODE=live, CFOS_LIVE_ACK=I_UNDERSTAND_REAL_MONEY and
   confirm_live=true. Paper is the default.
5. After a fill, report the position id, entry, stop and take-profit. If an order is
   partially filled or an exit fails to attach, say so explicitly.
6. cfos_kill is always allowed, by anyone, at any time.
"""

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string", "description": "CCXT unified swap, e.g. BTC/USDT:USDT"},
        "side": {"type": "string", "enum": ["long", "short"]},
        "entry": {"type": "number"},
        "stop": {"type": "number"},
        "take_profits": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 1,
        },
        "risk_pct": {"type": "number", "description": "percent of equity at risk"},
        "leverage": {"type": "number"},
        "intent": {"type": "string"},
        "rationale": {"type": "string"},
        "invalidation": {"type": "string"},
    },
    "required": ["symbol", "side", "entry", "stop", "take_profits"],
    "additionalProperties": False,
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "cfos_status",
        "description": "Account equity, drawdown, heat, open positions, kill switch, performance.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "cfos_doctor",
        "description": "Check Python, ccxt, config, credentials, journal and kill switch.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "cfos_market",
        "description": "Price, funding rate, open interest, 24h volume and spread for one symbol.",
        "inputSchema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "cfos_regime",
        "description": "Classify a symbol as trend_up, trend_down, range, squeeze or chaos with EMA/ATR/funding evidence.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "timeframe": {"type": "string", "default": "4h"},
                "limit": {"type": "integer", "minimum": 60, "maximum": 1000},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "cfos_allocate",
        "description": "Regime-aware strategy selection. Returns NO_TRADE unless a measured, confirmed edge exists for the current regime.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "regime": {
                    "type": "string",
                    "enum": ["trend_up", "trend_down", "range", "squeeze",
                             "chaos", "warmup"],
                },
                "symbol": {"type": "string", "default": "BTC/USDT:USDT"},
                "timeframe": {"type": "string", "default": "4h"},
                "allow_candidate": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "cfos_route",
        "description": "Map an intent (or free text) to the skill chain and gates to run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": [
                        "regime", "trend", "breakout", "mean_reversion", "funding_carry",
                        "liquidation_reversal", "grid", "news", "hedge", "size",
                        "review", "backtest", "ops",
                    ],
                },
                "text": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "cfos_size",
        "description": "Risk-based size only. Returns qty, notional, leverage, risk and liquidation price.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "side": {"type": "string", "enum": ["long", "short"]},
                "entry": {"type": "number"},
                "stop": {"type": "number"},
                "risk_pct": {"type": "number"},
            },
            "required": ["symbol", "side", "entry", "stop"],
            "additionalProperties": False,
        },
    },
    {
        "name": "cfos_plan",
        "description": "Build a sized bracket plan without sending an order.",
        "inputSchema": PLAN_SCHEMA,
    },
    {
        "name": "cfos_execute",
        "description": "Send the bracket order. All risk gates run first and can block it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                **PLAN_SCHEMA["properties"],
                "confirm_live": {"type": "boolean"},
                "dry_run": {"type": "boolean"},
            },
            "required": PLAN_SCHEMA["required"],
            "additionalProperties": False,
        },
    },
    {
        "name": "cfos_positions",
        "description": "Open positions with entry, mark, stop, take-profit and unrealized PnL.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "cfos_close",
        "description": "Close all or part of a position at market (reduce-only).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "qty": {"type": "number"},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "cfos_tick",
        "description": "Paper only: set a price, trigger stops/take-profits, reconcile the journal.",
        "inputSchema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}, "price": {"type": "number"}},
            "required": ["symbol", "price"],
            "additionalProperties": False,
        },
    },
    {
        "name": "cfos_kill",
        "description": "Engage the kill switch. Always allowed; blocks all new risk.",
        "inputSchema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "cfos_unkill",
        "description": "Release the kill switch. Use only after the cause is understood.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "cfos_journal",
        "description": "Closed trades, open theses and recent decisions.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}},
            "additionalProperties": False,
        },
    },
    {
        "name": "cfos_review",
        "description": "Performance summary: win rate, expectancy, profit factor, average R.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}},
            "additionalProperties": False,
        },
    },
]


def _text(payload: Any, *, error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": dumps(payload, indent=2)}],
        "isError": error,
    }


def _plan_from_args(settings: Settings, args: dict[str, Any]):
    exchange = build_exchange(settings)
    return build_plan(
        settings,
        exchange,
        symbol=str(args["symbol"]),
        side=Side(str(args["side"])),
        entry=float(args["entry"]),
        stop=float(args["stop"]),
        take_profits=[float(x) for x in args["take_profits"]],
        risk_pct=float(args["risk_pct"]) if args.get("risk_pct") is not None else None,
        leverage=float(args["leverage"]) if args.get("leverage") is not None else None,
        intent=str(args.get("intent") or "manual"),
        rationale=str(args.get("rationale") or ""),
        invalidation=str(args.get("invalidation") or ""),
    )


def call_tool(settings: Settings, name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "cfos_status":
        return _text(status(settings))

    if name == "cfos_doctor":
        checks = doctor_mod.run(settings)
        return _text({"status": doctor_mod.worst(checks), "checks": checks})

    if name == "cfos_market":
        snapshot = public_market_snapshot(settings, str(args["symbol"]))
        return _text(
            {
                "symbol": snapshot.symbol,
                "price": snapshot.price,
                "funding_rate": snapshot.funding_rate,
                "open_interest": snapshot.open_interest,
                "quote_volume_24h": snapshot.quote_volume_24h,
                "spread_bps": snapshot.spread_bps,
                "ts": snapshot.ts,
            }
        )

    if name == "cfos_regime":
        from .analysis import compute_regime

        exchange = build_exchange(settings)
        timeframe = str(args.get("timeframe") or "4h")
        candles = exchange.ohlcv(
            str(args["symbol"]), timeframe, int(args.get("limit") or 200)
        )
        snapshot = public_market_snapshot(settings, str(args["symbol"]))
        report = compute_regime(
            candles,
            symbol=str(args["symbol"]),
            timeframe=timeframe,
            funding_rate=snapshot.funding_rate,
        )
        return _text(report.to_dict())

    if name == "cfos_allocate":
        from .analysis import compute_regime

        if args.get("regime"):
            regime = str(args["regime"])
        else:
            symbol = str(args.get("symbol") or "BTC/USDT:USDT")
            timeframe = str(args.get("timeframe") or "4h")
            exchange = build_exchange(settings)
            candles = exchange.ohlcv(symbol, timeframe, 200)
            snapshot = public_market_snapshot(settings, symbol)
            regime = compute_regime(
                candles, symbol=symbol, timeframe=timeframe,
                funding_rate=snapshot.funding_rate,
            ).regime
        engaged, reason = KillSwitch(settings.kill_file).engaged()
        allocation = allocate(
            regime,
            load_registry(),
            kill_engaged=engaged,
            allow_candidate=bool(args.get("allow_candidate")),
        )
        payload = allocation.to_dict()
        payload["kill_switch"] = {"engaged": engaged, "reason": reason}
        return _text(payload)

    if name == "cfos_route":
        plan = route(intent=args.get("intent"), text=args.get("text"))
        return _text(
            {"intent": plan.intent, "skills": plan.skills, "gates": plan.gates, "note": plan.note}
        )

    if name == "cfos_size":
        exchange = build_exchange(settings)
        from .risk import position_size

        resolved = exchange.resolve_symbol(str(args["symbol"]))
        result = position_size(
            equity=exchange.balance(),
            risk_pct=float(args.get("risk_pct") or settings.risk.risk_per_trade_pct),
            entry=float(args["entry"]),
            stop=float(args["stop"]),
            side=Side(str(args["side"])),
            contract_size=exchange.contract_size(resolved),
            qty_step=exchange.qty_step(resolved),
            min_qty=exchange.min_qty(resolved),
            max_leverage=settings.risk.max_leverage,
            maintenance_margin_rate=settings.risk.maintenance_margin_rate,
        )
        return _text(result)

    if name == "cfos_plan":
        plan = _plan_from_args(settings, args)
        return _text(plan.to_dict())

    if name == "cfos_execute":
        plan = _plan_from_args(settings, args)
        report = execute(
            settings,
            plan,
            confirm_live=bool(args.get("confirm_live")),
            dry_run=bool(args.get("dry_run")),
        )
        return _text(report)

    if name == "cfos_positions":
        return _text([p for p in status(settings)["positions"]])

    if name == "cfos_close":
        return _text(
            close_position(
                settings,
                str(args["symbol"]),
                float(args["qty"]) if args.get("qty") is not None else None,
            )
        )

    if name == "cfos_tick":
        if settings.mode.value != "paper":
            return _text("tick is paper-only", error=True)
        paper = PaperExchange(settings)
        paper.set_price(str(args["symbol"]), float(args["price"]))
        synced = sync_paper(settings)
        return _text(
            {"symbol": args["symbol"], "price": args["price"], "closed": synced,
             "equity": paper.equity()}
        )

    if name == "cfos_kill":
        KillSwitch(settings.kill_file).engage(str(args.get("reason") or "agent kill"))
        return _text({"kill_switch": "ENGAGED", "reason": args.get("reason")})

    if name == "cfos_unkill":
        removed = KillSwitch(settings.kill_file).release()
        return _text({"kill_switch": "released" if removed else "was not engaged"})

    if name == "cfos_journal":
        journal = Journal(settings.db_path)
        limit = int(args.get("limit") or 20)
        return _text(
            {
                "summary": journal.summary(),
                "open": journal.open_positions(),
                "closed": journal.closed_positions(limit=limit),
                "decisions": journal.recent_decisions(limit=limit),
            }
        )

    if name == "cfos_review":
        journal = Journal(settings.db_path)
        limit = int(args.get("limit") or 50)
        return _text(
            {"summary": journal.summary(), "decisions": journal.recent_decisions(limit=limit)}
        )

    raise KeyError(f"unknown tool {name!r}")


def handle(settings: Settings, message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}

    if request_id is None and method and method.startswith("notifications/"):
        return None

    def ok(result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def err(code: int, text: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": text}}

    if method == "initialize":
        return ok(
            {
                "protocolVersion": params.get("protocolVersion") or "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "cfos", "version": __version__},
                "instructions": INSTRUCTIONS,
            }
        )

    if method == "ping":
        return ok({})

    if method == "tools/list":
        return ok({"tools": TOOLS})

    if method == "tools/call":
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        try:
            return ok(call_tool(settings, name, args))
        except KeyError:
            return err(-32601, f"unknown tool: {name}")
        except Exception as exc:  # noqa: BLE001 - a tool error is a tool result
            print(f"[cfos] tool {name} failed: {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            return ok(_text(f"{type(exc).__name__}: {exc}", error=True))

    if method in ("resources/list", "prompts/list"):
        key = "resources" if method.startswith("resources") else "prompts"
        return ok({key: []})

    return err(-32601, f"method not found: {method}")


def serve(settings: Settings) -> None:
    print(f"[cfos] mcp server v{__version__} mode={settings.mode.value}", file=sys.stderr)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"[cfos] bad JSON: {exc}", file=sys.stderr)
            continue
        if not isinstance(message, dict):
            continue
        response = handle(settings, message)
        if response is None:
            continue
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()
