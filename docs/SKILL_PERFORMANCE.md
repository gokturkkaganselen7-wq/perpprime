# PerpPrime — Skill Performance Matrix

**Date:** 2026-11-22 · **Mode:** paper only

> The repo ships 24 skills. Only **four** make a mechanical, backtestable
> PnL claim. All four lose money after the look-ahead fix. The other
> twenty are infrastructure, validation or analysis; they are verified by
> tests and audits, not by returns. "24 skills" is not "24 strategies".

## 1. What counts as measured

A skill is counted as measured only if all three hold:

1. its entry and exit rules are mechanical enough to code,
2. historical data for those rules exists in this repo,
3. the run is reproducible with `scripts/backtest_trend.py` or
   `scripts/backtest_edges.py`.

Anything else is labelled **not measured** — not because it is useless,
but because a PnL number would be invented.

## 2. The four measured strategies

| Skill | Strategy proxy | 12m trades | 12m avg return | OOS avg / window | Positive windows | Verdict |
|---|---|---:|---:|---:|---:|---|
| `cfos-trend` | Trend continuation | 353 | **-5.38%** | **-0.53%** | 3/4/3 | NO EDGE |
| `cfos-funding-carry` | Funding carry | 625 | **-8.46%** | **-1.04%** | 2/4/2 | NO EDGE |
| `cfos-breakout` | Opening-range breakout | 1002 | **-22.94%** | **-2.44%** | 3/3/2 | NO EDGE |
| `cfos-mean-reversion` | Range mean reversion | 187 | **-3.90%** | **-0.98%** | 1/3/3 | NO EDGE |

`cfos-breakout` is proxied by the opening-range breakout, `cfos-mean-reversion`
by the range fade, `cfos-funding-carry` by the 8h funding harvest. The full
rules, costs and failed criteria are in [EDGES_2026-11-22.md](EDGES_2026-11-22.md);
the trend rules are in [BACKTEST.md](BACKTEST.md).

**No strategy passes.** All four have negative out-of-sample averages and
fewer than 6 of 9 positive windows per symbol.

## 3. Full matrix

| # | Skill | Category | Measured | Evidence |
|---:|---|---|---|---|
| 1 | `cfos-trend` | Strategy | Yes | BACKTEST.md — 12/15 one-hour cells negative |
| 2 | `cfos-funding-carry` | Strategy | Yes | EDGES — all three symbols negative |
| 3 | `cfos-breakout` | Strategy | Yes (ORB proxy) | EDGES — negative, deep drawdowns |
| 4 | `cfos-mean-reversion` | Strategy | Yes (range fade) | EDGES — negative on all symbols |
| 5 | `cfos-grid` | Strategy | No | Multi-level fill engine and OI history missing |
| 6 | `cfos-liquidation-reversal` | Strategy | No | No historical liquidation/OI prints |
| 7 | `cfos-regime` | Component | Yes (ablation) | BACKTEST — `--no-regime` ablation; effect varies by symbol |
| 8 | `cfos-exit-management` | Component | Yes (inside trend) | BACKTEST — TP1/breakeven/stop; same-bar cases counted |
| 9 | `cfos-backtest` | Validation | Method | `scripts/backtest_trend.py`, `tests/test_backtest.py` |
| 10 | `cfos-walk-forward` | Validation | Method | 90d/30d/30d windows in both reports |
| 11 | `cfos-edge-review` | Validation | Method | Charter §5 criteria, applied in EDGES §5 |
| 12 | `cfos-postmortem` | Validation | No PnL claim | Journal-based review; no historical thesis log |
| 13 | `cfos-derivatives-sentiment` | Analysis | No | Funding is used; OI history is not available |
| 14 | `cfos-liquidation-map` | Analysis | No | Needs live order book / liquidation bands |
| 15 | `cfos-macro-calendar` | Analysis | No | No event calendar in the data set |
| 16 | `cfos-news-catalyst` | Analysis | No | No news feed in the data set |
| 17 | `cfos-execution` | Infrastructure | Tests | `tests/test_execution.py` — 3 state checks + emergency close |
| 18 | `cfos-risk-gate` | Infrastructure | Tests | `tests/test_risk.py` — every gate blocks |
| 19 | `cfos-sizing` | Infrastructure | Tests | `tests/test_risk.py`, `test_execution.py` |
| 20 | `cfos-journal` | Infrastructure | Tests | `tests/test_journal.py` |
| 21 | `cfos-portfolio-state` | Infrastructure | Tests | `cfos status --json`, execution tests |
| 22 | `cfos-ops` | Infrastructure | Checks | `make doctor` — OK |
| 23 | `cfos-router` | Infrastructure | Checks | `make audit` — 24/24 IN SYNC |
| 24 | `cfos-data-quality` | Infrastructure | Checks | stale/spread gates in `risk.py` |

## 4. Component evidence

- **Regime filter:** the trend ablation (`--no-regime`) is in the backtest
  JSON. It changes results but does not create a positive expectancy.
- **Exit management:** TP1 at 2R, stop to breakeven, gap-aware stops and
  same-bar ambiguity are all in the trend engine and tested.
- **Execution:** rejected stop, cancelled-after-poll stop and rejected TP
  each force an emergency close (three regression tests).
- **Risk gates:** unknown spread now blocks, equity includes unrealised
  PnL, partial fills are reconciled to the venue size.

## 5. What remains unmeasured

`cfos-grid` and `cfos-liquidation-reversal` are mechanical enough to test
but the data or engine is missing. Building a grid engine without
order-book fills would produce a flattering number, and a liquidation
reversal test without liquidation prints would be a proxy of a proxy.
Both are deliberately left unmeasured until the data exists.

## 6. Implication

- The **infrastructure** claim is supported: routing, risk gates, sizing,
  execution, journal and audit are exercised by 135 tests and three
  operational checks.
- The **strategy** claim is not: four mechanical hypotheses, four
  negative out-of-sample results.
- The repo is a research platform with honest guardrails, not a
  profitable trading system. Paper mode remains the default.
