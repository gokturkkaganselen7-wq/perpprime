# CFOS v1 — Architecture

## 1. Layer map

```
┌──────────────────────────────────────────────────────────────┐
│ Agents: OpenCode · Claude Code · Codex                       │
│   skills/*.md  +  MCP tools  +  CLI                          │
└───────────────┬──────────────────────────────────────────────┘
                │ stdio JSON-RPC 2.0
┌───────────────▼──────────────────────────────────────────────┐
│ src/cfos/mcp_server.py    15 tools, no SDK dependency        │
│ src/cfos/cli.py           same operations for humans         │
└───────────────┬──────────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────┐
│ execution.py   plan builder, account state, the ONLY order   │
│                path: gates → permission → entry → stop → TP  │
│ router.py      intent → skill chain + gate list              │
│ analysis.py    deterministic regime classifier               │
└───────┬───────────────────┬───────────────────┬──────────────┘
        │                   │                   │
┌───────▼───────┐   ┌───────▼───────┐   ┌───────▼──────────────┐
│ risk.py       │   │ guards.py     │   │ journal.py           │
│ pure math,    │   │ kill switch,  │   │ SQLite: decisions,   │
│ 15 gates,     │   │ live locks,   │   │ orders, positions,   │
│ fail closed   │   │ preflight     │   │ equity               │
└───────────────┘   └───────────────┘   └──────────────────────┘
        │
┌───────▼──────────────────────────────────────────────────────┐
│ exchange.py   BaseExchange → CcxtExchange                    │
│ paper.py      PaperExchange (deterministic simulator)        │
│ config.py     TOML + env, no PyYAML                          │
└──────────────────────────────────────────────────────────────┘
```

## 2. Why MCP instead of an agent-specific plugin

The three target agents do not share a plugin API. They do share MCP over
stdio. A single Python server therefore gives identical behaviour everywhere,
with no node/npm requirement and no duplicated execution logic. Skills are
plain `SKILL.md` files, readable by any agent that supports project
instructions; symlinks expose the same bundle to OpenCode and Claude Code.

## 3. Execution sequence

```
cfos plan
  resolve symbol → contract size / qty step / min qty
  equity = exchange.balance()
  size = position_size(equity, risk_pct, entry, stop, …)
  rr = reward_risk(entry, stop, tp1)
  write plan JSON (no order)

cfos execute --plan
  build exchange from mode (paper | testnet | live)
  paper: seed price at plan.entry if absent (offline determinism)
  snapshot = exchange.market(symbol)
  state = account_state(journal, exchange)
  gates = evaluate_gates(…15 rules…)
  if dry_run → DRY_RUN (journal decision, no orders)
  if any BLOCK → BLOCKED (journal decision)
  check_live_permission(mode, ack, confirm_live)
  set_leverage
  entry = market order (client_id = plan.id-entry)
  entry = confirm_entry(entry)     # poll fetch_order when status/filled is absent
  filled_qty = actual fill (plan.qty when status is closed but filled is omitted)
  if fill unconfirmed → ERROR; check venue positions and defensive close if any
  paper: tag position with plan.id + risk_amount
  journal.open_position(qty=filled_qty, ...)   # before bracket, so an emergency close reconciles
  stop = reduce-only stop_market at plan.stop, amount=filled_qty
  qty slices = split_quantity(filled_qty, len(take_profits), min_qty, qty_step)
  for tp, slice in zip(take_profits, qty slices): reduce-only take_profit_market
  journal.snapshot_equity(...)
  return EXECUTED

A partial entry is not an error: the bracket is attached to the filled quantity
and the report carries a warning. Any exception after the entry was attempted
triggers a venue position check and, if a position exists, a defensive close —
so a half-built bracket never leaves a naked position behind.
```

**Invariant:** there is no second code path that reaches `create_order`.

## 4. Gate order

`kill_switch → universe → daily_loss → weekly_loss → drawdown →
trade_count → max_positions → max_per_symbol → correlation →
portfolio_heat → cooldown → reward_risk → data_freshness → spread →
leverage`

The order is asserted by tests. `blocked()` returns every BLOCK, not just the
first, so one response explains all reasons.

## 5. Account state assembly

`account_state()` combines:

- venue equity and open positions (`exchange.balance/positions`)
- journal open risk (`positions.risk_amount` where status=open)
- journal peak equity and day/week start equity (`equity` table)
- consecutive losses and last loss timestamp (`positions.pnl`)
- cluster counts from `config/universe.toml`

Missing data never becomes "zero risk": the corresponding gate blocks or
warns. For example, `market_age_sec=None` blocks `data_freshness`.

## 6. Paper engine

`PaperExchange` persists a single JSON document (`data/paper_state.json`):

```json
{
  "cash": 9945.16,
  "realized": -53.05,
  "fees": 1.79,
  "positions": {"BTC/USDT:USDT": {...}},
  "orders": {},
  "prices": {"BTC/USDT:USDT": 64000.0},
  "leverage": {"BTC/USDT:USDT": 2.0},
  "trades": [...]
}
```

Behaviour:

- Market fills slip by `paper_slippage_bps` in the adverse direction.
- Fees are charged on entry and exit at `taker_fee_pct`.
- Stop/TP orders are stored on the position; `set_price()` processes them.
  Take-profits form a ladder: each level carries its own slice quantity and
  fires independently.
- Liquidation is checked before stop/TP.
- Every close appends a trade with `position_id`, then `drain_trades()` lets
  the journal consume it exactly once.

The JSON write is atomic (`tmp` + `os.replace`).

## 7. Journal schema

| Table | Key | Purpose |
|---|---|---|
| `decisions` | autoincrement | Every plan/execute outcome, including BLOCKED and DRY_RUN |
| `orders` | `client_id` unique | Entry/stop/TP lifecycle, upsert on status change |
| `positions` | plan id | Open/closed trade records with risk, fees, PnL |
| `equity` | timestamp (µs) | Equity curve for drawdown and review |

`summary()` computes win rate, expectancy, profit factor, average R and fees
from closed positions only.

## 8. Configuration

- `config/risk.toml` — all limits, fees, slippage, data age, retries.
- `config/universe.toml` — allowlist, clusters, venue defaults.
- Env vars override mode, venue, equity, credentials, paths.
- No YAML, no dotenv parser: `tomllib` + `os.environ`.

## 9. Failure modes and responses

| Failure | Behaviour |
|---|---|
| ccxt missing | `doctor` FAIL for testnet/live, WARN for paper; trades that need public data fail closed |
| venue timeout | retried only for transient errors; otherwise `ERROR` |
| entry response lost / fill unconfirmed | `ERROR`; venue positions are inspected and a defensive close is sent only if a position exists |
| entry partially filled | `EXECUTED` with a warning; stop and TP ladder cover the filled quantity exactly |
| stop/TP attach fails | `ERROR`; an emergency reduce-only close is submitted automatically. If that close also fails, the message says so explicitly |
| paper state corrupt | `ExchangeError`; no silent reset |
| kill file unreadable | treated as engaged |
| journal locked | SQLite timeout (10s), then error |

## 10. Extension points

- New venue: add a CCXT id to `config/universe.toml`; no code change.
- New symbol: add to `universe.allow` and optionally a cluster.
- New intent: add to `router.INTENTS`, a keyword list, a skill directory, and
  the registry table; `test_router.py` enforces coverage.
- New gate: implement in `risk.evaluate_gates`, add to `GATES`, test both ALLOW
  and BLOCK.
- New MCP tool: add to `TOOLS` and `call_tool`; stdout stays protocol-only.

## 11. Deliberate non-goals for v1

No backtest engine, no historical funding/OI store, no hedge-mode support, no
withdrawal capability, no multi-account aggregation, no web UI. Every one of
these would add a failure surface before the core execution path is proven.
