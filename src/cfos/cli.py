"""CFOS command line. This is the surface both humans and agents use."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import __version__, doctor as doctor_mod
from .analysis import compute_regime
from .config import ConfigError, Settings, load_settings
from .execution import (
    account_state,
    build_plan,
    close_position,
    execute,
    status,
    sync_paper,
)
from .exchange import build_exchange, public_market_snapshot
from .guards import KillSwitch
from .journal import Journal
from .models import Mode, Side, TradePlan, dumps
from .paper import PaperExchange
from .risk import position_size, symbol_in_universe
from .router import catalogue, route


def _print_table(rows: list[dict[str, Any]], columns: list[str]) -> None:
    if not rows:
        print("(empty)")
        return
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in columns}
    print("  ".join(c.ljust(widths[c]) for c in columns))
    print("  ".join("-" * widths[c] for c in columns))
    for row in rows:
        print("  ".join(str(row.get(c, "")).ljust(widths[c]) for c in columns))


def _emit(payload: Any, as_json: bool) -> None:
    if as_json:
        print(dumps(payload))
        return
    print(payload if isinstance(payload, str) else dumps(payload))


def cmd_doctor(settings: Settings, args: argparse.Namespace) -> int:
    checks = doctor_mod.run(settings)
    if args.json:
        print(dumps({"status": doctor_mod.worst(checks), "checks": checks}))
    else:
        _print_table(checks, ["check", "status", "detail"])
        print(f"\nOverall: {doctor_mod.worst(checks)}")
    return 1 if doctor_mod.worst(checks) == "FAIL" else 0


def cmd_status(settings: Settings, args: argparse.Namespace) -> int:
    payload = status(settings)
    if args.json:
        print(dumps(payload))
        return 0
    account = payload["account"]
    print(
        f"mode={payload['mode']} venue={payload['venue']} "
        f"equity={account['equity']:.2f} dd={account['drawdown_pct']:.2f}% "
        f"heat={(account['open_risk'] / account['equity'] * 100) if account['equity'] else 0:.2f}%"
    )
    kill = payload["kill_switch"]
    print(f"kill_switch={'ENGAGED' if kill['engaged'] else 'released'}"
          + (f" ({kill['reason']})" if kill["engaged"] else ""))
    _print_table(
        payload["positions"],
        ["symbol", "side", "qty", "entry", "mark", "unrealized", "leverage"],
    )
    summary = payload["summary"]
    print(
        f"\ntrades={summary['trades']} win_rate={summary['win_rate']}% "
        f"expectancy={summary['expectancy']} profit_factor={summary['profit_factor']} "
        f"avg_R={summary['avg_r']}"
    )
    return 0


def cmd_route(settings: Settings, args: argparse.Namespace) -> int:
    if args.list:
        print(dumps(catalogue(), indent=2))
        return 0
    plan = route(intent=args.intent, text=args.text)
    if args.json:
        print(dumps(plan))
    else:
        print(f"intent: {plan.intent}")
        print(f"note  : {plan.note}")
        print("chain : " + " -> ".join(plan.skills))
        print("gates : " + ", ".join(plan.gates))
    return 0


def cmd_market(settings: Settings, args: argparse.Namespace) -> int:
    snapshot = public_market_snapshot(settings, args.symbol)
    payload = {
        "symbol": snapshot.symbol,
        "price": snapshot.price,
        "funding_rate": snapshot.funding_rate,
        "open_interest": snapshot.open_interest,
        "quote_volume_24h": snapshot.quote_volume_24h,
        "spread_bps": snapshot.spread_bps,
        "ts": snapshot.ts,
        "allowed": symbol_in_universe(snapshot.symbol, settings.universe),
    }
    _emit(payload, args.json)
    return 0


def cmd_regime(settings: Settings, args: argparse.Namespace) -> int:
    exchange = build_exchange(settings)
    candles = exchange.ohlcv(args.symbol, args.tf, args.limit)
    snapshot = public_market_snapshot(settings, args.symbol)
    report = compute_regime(
        candles,
        symbol=args.symbol,
        timeframe=args.tf,
        funding_rate=snapshot.funding_rate,
    )
    _emit(report.to_dict(), args.json)
    return 0


def cmd_candles(settings: Settings, args: argparse.Namespace) -> int:
    exchange = build_exchange(settings)
    rows = exchange.ohlcv(args.symbol, args.tf, args.limit)
    payload = [
        {"ts": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]}
        for r in rows
    ]
    if args.json:
        print(dumps(payload))
    else:
        _print_table(payload[-20:], ["ts", "open", "high", "low", "close", "volume"])
    return 0


def cmd_size(settings: Settings, args: argparse.Namespace) -> int:
    exchange = build_exchange(settings)
    resolved = exchange.resolve_symbol(args.symbol)
    result = position_size(
        equity=exchange.balance(),
        risk_pct=args.risk_pct,
        entry=args.entry,
        stop=args.stop,
        side=Side(args.side),
        contract_size=exchange.contract_size(resolved),
        qty_step=exchange.qty_step(resolved),
        min_qty=exchange.min_qty(resolved),
        max_leverage=settings.risk.max_leverage,
        maintenance_margin_rate=settings.risk.maintenance_margin_rate,
    )
    payload = {
        "symbol": resolved,
        "side": args.side,
        "ok": result.ok,
        "qty": result.qty,
        "notional": result.notional,
        "leverage": result.leverage,
        "risk_amount": result.risk_amount,
        "risk_pct": result.risk_pct,
        "stop_distance": result.stop_distance,
        "liq_price": result.liq_price,
        "warnings": result.warnings,
        "reason": result.reason,
    }
    _emit(payload, args.json)
    return 0 if result.ok else 1


def cmd_plan(settings: Settings, args: argparse.Namespace) -> int:
    exchange = build_exchange(settings)
    try:
        plan = build_plan(
            settings,
            exchange,
            symbol=args.symbol,
            side=Side(args.side),
            entry=args.entry,
            stop=args.stop,
            take_profits=args.tp,
            risk_pct=args.risk_pct,
            intent=args.intent,
            rationale=args.rationale,
            invalidation=args.invalidation,
            leverage=args.leverage,
        )
    except ValueError as exc:
        print(f"plan rejected: {exc}", file=sys.stderr)
        return 1
    if args.out:
        path = Path(args.out)
        path.write_text(dumps(plan.to_dict()), encoding="utf-8")
    if args.json:
        print(dumps(plan.to_dict()))
    else:
        print(
            f"{plan.symbol} {plan.side.value} qty={plan.qty} entry={plan.entry} "
            f"stop={plan.stop} tp={plan.take_profits} lev={plan.leverage:.2f}x "
            f"risk={plan.risk_amount:.2f} ({plan.risk_amount / exchange.balance() * 100:.2f}%) "
            f"R:R={plan.rr:.2f}"
        )
        print(f"id: {plan.id}")
        if plan.warnings:
            print("warnings: " + "; ".join(plan.warnings))
        if args.out:
            print(f"written: {args.out}")
    return 0


def _plan_from_file(path: Path) -> TradePlan:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["side"] = Side(data["side"])
    data["mode"] = Mode(data["mode"])
    return TradePlan(**data)


def cmd_execute(settings: Settings, args: argparse.Namespace) -> int:
    try:
        plan = _plan_from_file(Path(args.plan))
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"cannot read plan: {exc}", file=sys.stderr)
        return 2
    report = execute(
        settings, plan, confirm_live=args.confirm_live, dry_run=args.dry_run
    )
    payload = {
        "ok": report.ok,
        "verdict": report.verdict.value,
        "mode": report.mode.value,
        "symbol": report.symbol,
        "messages": report.messages,
        "position": report.position,
        "blocked": [g["name"] for g in report.gates if g["status"] == "BLOCK"],
        "warnings": [
            f"{g['name']}: {g['reason']}" for g in report.gates if g["status"] == "WARN"
        ],
    }
    if args.json:
        print(dumps(payload))
    else:
        print(f"verdict: {payload['verdict']} ({payload['mode']})")
        for message in payload["messages"]:
            print(f"  - {message}")
        if payload["blocked"]:
            print("blocked by: " + ", ".join(payload["blocked"]))
    return 0 if report.verdict.value in ("EXECUTED", "DRY_RUN") else 1


def cmd_positions(settings: Settings, args: argparse.Namespace) -> int:
    payload = status(settings)["positions"]
    if args.json:
        print(dumps(payload))
    else:
        _print_table(
            payload,
            ["symbol", "side", "qty", "entry", "mark", "unrealized", "stop", "take_profit"],
        )
    return 0


def cmd_close(settings: Settings, args: argparse.Namespace) -> int:
    try:
        result = close_position(settings, args.symbol, args.qty)
    except Exception as exc:  # noqa: BLE001
        print(f"close failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    _emit(result, args.json)
    return 0


def cmd_tick(settings: Settings, args: argparse.Namespace) -> int:
    if settings.mode is not Mode.PAPER:
        print("tick is a paper-only command", file=sys.stderr)
        return 2
    paper = PaperExchange(settings)
    paper.set_price(args.symbol, args.price)
    synced = sync_paper(settings)
    payload = {
        "symbol": args.symbol,
        "price": args.price,
        "equity": paper.equity(),
        "closed": synced,
    }
    _emit(payload, args.json)
    return 0


def cmd_kill(settings: Settings, args: argparse.Namespace) -> int:
    switch = KillSwitch(settings.kill_file)
    if args.release:
        removed = switch.release()
        print("kill switch released" if removed else "kill switch was not engaged")
        return 0
    switch.engage(args.reason)
    print(f"kill switch ENGAGED: {args.reason}")
    return 0


def cmd_journal(settings: Settings, args: argparse.Namespace) -> int:
    journal = Journal(settings.db_path)
    payload = {
        "summary": journal.summary(),
        "open": journal.open_positions(),
        "closed": journal.closed_positions(limit=args.limit),
        "decisions": journal.recent_decisions(limit=args.limit),
    }
    if args.json:
        print(dumps(payload))
    else:
        _print_table(
            payload["closed"],
            ["closed_at", "symbol", "side", "qty", "entry", "exit", "pnl"],
        )
        print()
        _print_table(payload["decisions"], ["ts", "intent", "symbol", "verdict"])
    return 0


def cmd_review(settings: Settings, args: argparse.Namespace) -> int:
    journal = Journal(settings.db_path)
    summary = journal.summary()
    decisions = journal.recent_decisions(limit=args.limit)
    verdicts: dict[str, int] = {}
    for decision in decisions:
        verdicts[decision["verdict"]] = verdicts.get(decision["verdict"], 0) + 1
    payload = {"summary": summary, "verdicts": verdicts, "decisions": decisions}
    if args.json:
        print(dumps(payload))
        return 0
    print(dumps(summary))
    print("recent verdicts: " + ", ".join(f"{k}={v}" for k, v in sorted(verdicts.items())))
    if summary["trades"] < 20:
        print("note: fewer than 20 closed trades — no statistical claim is valid yet")
    return 0


def cmd_demo(settings: Settings, args: argparse.Namespace) -> int:
    from .models import utcnow

    stamp = utcnow().replace(":", "").replace("-", "")[:15]
    demo_dir = settings.data_dir / "demo" / stamp
    demo = replace(
        settings,
        data_dir=demo_dir,
        db_path=demo_dir / "cfos.sqlite3",
        kill_file=demo_dir / "KILL",
    )
    demo.ensure_dirs()
    symbol = "BTC/USDT:USDT"

    paper = PaperExchange(demo)
    paper.reset(10_000)
    paper.set_price(symbol, 65_000)

    plan = build_plan(
        demo,
        paper,
        symbol=symbol,
        side=Side.LONG,
        entry=65_000,
        stop=64_000,
        take_profits=[67_000],
        risk_pct=0.5,
        intent="trend",
        rationale="demo: long above range, stop under structure",
        invalidation="4h close below 64,000",
    )
    report = execute(demo, plan)
    print(f"[1] entry      {report.verdict.value}: " + "; ".join(report.messages))

    exit_engine = PaperExchange(demo)
    exit_engine.set_price(symbol, 64_000)
    synced = sync_paper(demo)
    print(f"[2] stop tick  closed={len(synced)} trade(s)")

    final = PaperExchange(demo)
    journal = Journal(demo.db_path)
    summary = journal.summary()
    print(
        f"[3] result     equity={final.equity():.2f} pnl={summary['total_pnl']:.2f} "
        f"trades={summary['trades']} win_rate={summary['win_rate']}%"
    )
    print(f"    artifacts  {demo_dir}")
    return 0


def cmd_mcp(settings: Settings, args: argparse.Namespace) -> int:
    from .mcp_server import serve

    serve(settings)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cfos",
        description="Crypto futures operating system for coding agents",
    )
    parser.add_argument("--home", help="workspace root containing config/")
    parser.add_argument("--version", action="version", version=f"cfos {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="check environment and safety configuration")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("status", help="account, gates, positions and performance")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("route", help="map an intent to a skill chain")
    p.add_argument("--intent", choices=sorted(catalogue()))
    p.add_argument("--text")
    p.add_argument("--list", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_route)

    p = sub.add_parser("market", help="price, funding, OI and spread for a symbol")
    p.add_argument("symbol")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_market)

    p = sub.add_parser("regime", help="classify trend / range / squeeze / chaos")
    p.add_argument("symbol")
    p.add_argument("--tf", default="4h")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_regime)

    p = sub.add_parser("candles", help="raw OHLCV (mostly for debugging)")
    p.add_argument("symbol")
    p.add_argument("--tf", default="4h")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_candles)

    p = sub.add_parser("size", help="risk-based position size")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", choices=["long", "short"], required=True)
    p.add_argument("--entry", type=float, required=True)
    p.add_argument("--stop", type=float, required=True)
    p.add_argument("--risk-pct", type=float)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_size)

    p = sub.add_parser("plan", help="build a sized trade plan (no order)")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", choices=["long", "short"], required=True)
    p.add_argument("--entry", type=float, required=True)
    p.add_argument("--stop", type=float, required=True)
    p.add_argument("--tp", type=float, action="append", required=True)
    p.add_argument("--risk-pct", type=float)
    p.add_argument("--leverage", type=float)
    p.add_argument("--intent", default="manual")
    p.add_argument("--rationale", default="")
    p.add_argument("--invalidation", default="")
    p.add_argument("--out", help="write plan JSON to this path")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("execute", help="place a planned bracket order")
    p.add_argument("--plan", required=True, help="path to plan JSON")
    p.add_argument("--confirm-live", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_execute)

    p = sub.add_parser("positions", help="open positions")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_positions)

    p = sub.add_parser("close", help="close a position at market")
    p.add_argument("symbol")
    p.add_argument("--qty", type=float)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_close)

    p = sub.add_parser("tick", help="paper-only: set a price and process stops/TPs")
    p.add_argument("symbol")
    p.add_argument("price", type=float)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_tick)

    p = sub.add_parser("kill", help="engage or release the kill switch")
    p.add_argument("--reason", default="manual kill")
    p.add_argument("--release", action="store_true")
    p.set_defaults(func=cmd_kill)

    p = sub.add_parser("journal", help="closed trades and decisions")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_journal)

    p = sub.add_parser("review", help="performance summary and recent verdicts")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("demo", help="run a full paper lifecycle end to end")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("mcp", help="serve MCP over stdio")
    p.set_defaults(func=cmd_mcp)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.home)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    return int(args.func(settings, args) or 0)
