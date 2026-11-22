# PerpPrime — Trend Backtest Report

> **CORRECTION — 2026-11-22.** The first version of this report (commit
> `3cd415d`) was wrong. The engine filled entries at the **signal bar's
> open** instead of the **next bar's open**, a one-bar look-ahead that gave
> every trade a free bar body.
>
> The bug is fixed in `scripts/backtest_trend.py`;
> `tests/test_backtest.py` locks the behaviour down. Every number below was
> re-run from scratch with the fixed engine (second correction pass: gaps,
> liquidation, intrabar drawdown).
>
> **The previous results are retracted. After the fix the trend rules show
> no edge: 12 of 15 one-hour period/symbol cells are
> negative, profit factors are 0.39–1.00, and 12-month
> drawdowns reach 16.9%. Do not
> trade this strategy with real money.**

## TL;DR

- **12 of 15** one-hour cells are negative after the fix
  (v1.0.0 reported 15/15 positive).
- Profit factor is **0.39–1.00** in the losing cells; win
  rates fall to 22–38%.
- The only positive period is the one-month crash (2026-10-15 →
  2026-11-14), where 32 of 32 trades were short.
  That is regime beta, not edge.
- 12-month max drawdown is **7.7% / 10.3% / 16.9%** (BTC/ETH/SOL, intrabar).
  ETH and SOL sit above the 10% live limit.
- Walk-forward out-of-sample is negative for all three symbols.
- Scaling risk cannot fix a negative expectancy; it only enlarges losses.

## 1. What was tested

| Component | Rule |
|---|---|
| Venue / data | Bybit USDT perpetuals, real OHLCV (1h and 15m) |
| Regime filter | 4h CFOS regime (EMA20/50/200 + ATR + 20-bar range); only `trend_up` / `trend_down` |
| Entry trigger | Close crosses back above EMA20 after dipping below it (mirrored for shorts) |
| Fill | **Next bar's open** after the signal bar closes |
| Stop | Last 10-bar swing ± 0.5 ATR |
| TP1 | 2R, 50% closed, stop moved to breakeven |
| TP2 | Measured move (20-bar range), clamped to 3R–8R |
| Other exits | Regime flip, 4h EMA50 break, 2-day time stop, window end |
| Costs | Taker 5.5 bps + 2 bps slippage per fill + **real Bybit funding** |
| Look-ahead | Signal on a closed bar, fill at the next bar open |
| Ambiguous bar | If stop and target are both inside one bar, the **stop** is assumed first |
| Gap risk | A bar that opens through the stop fills at the **open**, not the stop |
| Liquidation | Isolated-margin approximation, MMR 0.5%; a gap through it exits at the open with a 55 bps liquidation fee |
| Drawdown | Marked intrabar at the worst plausible point, floored at the stop |
| Warm-up | 75 days (required for 4h EMA200); never traded |
| Sizing | 0.5% equity risk per trade, 3x leverage cap |
| Starting equity | 10,000 USDT per run |

## 2. The correction

The v1.0.0 engine did this:

```python
entry = slip(o, side, entering=True)   # o = SIGNAL bar open
pos = Position(entry_ts=rows[i + 1][0], ...)  # timestamp = NEXT bar
```

The signal only exists once bar `i` closes (`c > EMA20`), so filling at
bar `i`'s open is look-ahead. Because the signal bar is directional by
construction, the bug handed every trade roughly one bar body of free
profit. The fix is one line:

```python
entry = slip(rows[i + 1][1], side, entering=True)  # next bar open
```

A second pass closed the modelling shortcuts that flattered the numbers:
stops now respect gaps, positions can be liquidated, drawdown is measured
intrabar, and bars where TP1 and breakeven are both inside one candle are
counted instead of silently resolved in the strategy's favour.

| Period | Symbol | v1.0.0 return | Corrected | v1.0.0 PF | Corrected PF | v1.0.0 DD | Corrected DD |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 month | BTC | +3.30% | **+1.47%** | 2.78 | 1.79 | 1.25% | 1.86% |
| 1 month | ETH | +7.69% | **+1.25%** | 5.20 | 1.52 | 1.23% | 2.54% |
| 1 month | SOL | +13.55% | **+5.44%** | 10.14 | 4.28 | 0.56% | 1.60% |
| 12 months | BTC | +32.54% | **-0.13%** | 1.87 | 1.00 | 3.64% | 7.72% |
| 12 months | ETH | +42.30% | **-5.44%** | 2.13 | 0.86 | 4.19% | 10.29% |
| 12 months | SOL | +42.06% | **-10.59%** | 1.96 | 0.73 | 3.45% | 16.85% |
| Bull 2023 | BTC | +20.01% | **-6.45%** | 1.55 | 0.81 | 6.65% | 10.45% |
| Bull 2023 | ETH | +2.51% | **-17.91%** | 1.06 | 0.54 | 6.61% | 20.36% |
| Bull 2023 | SOL | +25.62% | **-5.54%** | 1.61 | 0.84 | 2.85% | 10.05% |
| Bull 2024 | BTC | +31.37% | **-9.31%** | 1.93 | 0.73 | 3.73% | 14.36% |
| Bull 2024 | ETH | +15.60% | **-13.91%** | 1.33 | 0.61 | 5.87% | 17.00% |
| Bull 2024 | SOL | +26.19% | **-2.34%** | 1.76 | 0.93 | 3.55% | 8.66% |
| Correction 2024 | BTC | +12.43% | **-8.96%** | 1.73 | 0.54 | 3.73% | 10.01% |
| Correction 2024 | ETH | +7.05% | **-11.05%** | 1.33 | 0.39 | 3.57% | 11.67% |
| Correction 2024 | SOL | +9.55% | **-6.00%** | 1.53 | 0.66 | 2.68% | 8.66% |

Every walk-forward and ablation result in v1.0.0 was produced by the same
buggy engine, so those conclusions are retracted as well.

## 3. Results (corrected)

### 3.1 1 month (2026-10-15 → 2026-11-14)

**1h**

| Symbol | Trades | Long/Short | Win | Avg R | PF | Max DD | DD closed | Return | Fees | Funding | Buy & hold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 9 | 0/9 | 55.6% | +0.33 | 1.79 | 1.86% | 1.30% | **+1.47%** | 54.6 | 9.4 | -16.3% |
| ETH | 10 | 0/10 | 50.0% | +0.25 | 1.52 | 2.54% | 1.81% | **+1.25%** | 51.0 | 4.6 | -20.2% |
| SOL | 13 | 0/13 | 76.9% | +0.82 | 4.28 | 1.60% | 0.54% | **+5.44%** | 53.1 | 5.9 | -25.5% |
| **Total** | **32** | | **62.5%** | | | | | **avg 2.72%** | **159** | **20** | |

**15m**

| Symbol | Trades | Long/Short | Win | Avg R | PF | Max DD | DD closed | Return | Fees | Funding | Buy & hold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 20 | 1/19 | 45.0% | +0.03 | 1.04 | 2.90% | 2.38% | **+0.26%** | 211.2 | 9.4 | -16.3% |
| ETH | 41 | 0/41 | 41.5% | -0.03 | 0.95 | 6.43% | 5.95% | **-0.73%** | 372.9 | 7.1 | -20.2% |
| SOL | 42 | 0/42 | 42.9% | +0.22 | 1.34 | 2.53% | 2.23% | **+4.50%** | 316.4 | 9.7 | -25.5% |
| **Total** | **103** | | **42.7%** | | | | | **avg 1.34%** | **901** | **26** | |

### 3.2 12 months (2025-11-14 → 2026-11-14)

**1h**

| Symbol | Trades | Long/Short | Win | Avg R | PF | Max DD | DD closed | Return | Fees | Funding | Buy & hold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 107 | 40/67 | 35.5% | +0.00 | 1.00 | 7.72% | 7.51% | **-0.13%** | 622.0 | 12.0 | -31.4% |
| ETH | 122 | 50/72 | 34.4% | -0.09 | 0.86 | 10.29% | 9.53% | **-5.44%** | 510.5 | -0.6 | -40.2% |
| SOL | 124 | 49/75 | 32.3% | -0.18 | 0.73 | 16.85% | 16.70% | **-10.59%** | 435.3 | -2.4 | -46.3% |
| **Total** | **353** | | **34.0%** | | | | | **avg -5.38%** | **1568** | **9** | |

### 3.3 Bull 2023 (2023-01-01 → 2024-01-01)

**1h**

| Symbol | Trades | Long/Short | Win | Avg R | PF | Max DD | DD closed | Return | Fees | Funding | Buy & hold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 102 | 77/25 | 38.2% | -0.13 | 0.81 | 10.45% | 10.09% | **-6.45%** | 591.5 | -56.2 | +156.9% |
| ETH | 108 | 65/43 | 25.0% | -0.36 | 0.54 | 20.36% | 19.87% | **-17.91%** | 582.1 | -7.8 | +92.0% |
| SOL | 109 | 68/41 | 33.0% | -0.10 | 0.84 | 10.05% | 9.81% | **-5.54%** | 270.8 | -15.2 | +924.4% |
| **Total** | **319** | | **32.0%** | | | | | **avg -9.97%** | **1444** | **-79** | |

### 3.4 Bull 2024 (2024-01-01 → 2025-01-01)

**1h**

| Symbol | Trades | Long/Short | Win | Avg R | PF | Max DD | DD closed | Return | Fees | Funding | Buy & hold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 107 | 76/31 | 34.6% | -0.18 | 0.73 | 14.36% | 14.09% | **-9.31%** | 450.1 | -93.1 | +122.9% |
| ETH | 114 | 58/56 | 29.8% | -0.26 | 0.61 | 17.00% | 16.82% | **-13.91%** | 375.4 | -54.8 | +47.2% |
| SOL | 102 | 63/39 | 33.3% | -0.04 | 0.93 | 8.66% | 8.43% | **-2.34%** | 237.7 | -65.0 | +88.9% |
| **Total** | **323** | | **32.5%** | | | | | **avg -8.52%** | **1063** | **-213** | |

### 3.5 Correction 2024 (2024-04-01 → 2024-10-01)

**1h**

| Symbol | Trades | Long/Short | Win | Avg R | PF | Max DD | DD closed | Return | Fees | Funding | Buy & hold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 53 | 26/27 | 26.4% | -0.35 | 0.54 | 10.01% | 10.01% | **-8.96%** | 228.6 | -9.7 | -11.0% |
| ETH | 55 | 16/39 | 21.8% | -0.42 | 0.39 | 11.67% | 11.62% | **-11.05%** | 171.4 | -0.7 | -28.3% |
| SOL | 50 | 19/31 | 24.0% | -0.24 | 0.66 | 8.66% | 8.43% | **-6.00%** | 130.7 | -7.5 | -24.0% |
| **Total** | **158** | | **24.1%** | | | | | **avg -8.67%** | **531** | **-18** | |

## 4. Strategy vs buy & hold

| Period | Symbol | Strategy | Strategy DD | Strategy ret/DD | Buy & hold | B&H DD |
|---|---|---:|---:|---:|---:|---:|
| 1 month | BTC | +1.47% | 1.86% | **0.79** | -16.3% | 19.70% |
| 1 month | ETH | +1.25% | 2.54% | **0.49** | -20.2% | 23.99% |
| 1 month | SOL | +5.44% | 1.60% | **3.40** | -25.5% | 28.49% |
| 12 months | BTC | -0.13% | 7.72% | **-0.02** | -31.4% | 41.49% |
| 12 months | ETH | -5.44% | 10.29% | **-0.53** | -40.2% | 55.12% |
| 12 months | SOL | -10.59% | 16.85% | **-0.63** | -46.3% | 58.77% |
| Bull 2023 | BTC | -6.45% | 10.45% | **-0.62** | +156.9% | 21.85% |
| Bull 2023 | ETH | -17.91% | 20.36% | **-0.88** | +92.0% | 28.40% |
| Bull 2023 | SOL | -5.54% | 10.05% | **-0.55** | +924.4% | 47.30% |
| Bull 2024 | BTC | -9.31% | 14.36% | **-0.65** | +122.9% | 32.41% |
| Bull 2024 | ETH | -13.91% | 17.00% | **-0.82** | +47.2% | 46.74% |
| Bull 2024 | SOL | -2.34% | 8.66% | **-0.27** | +88.9% | 46.61% |
| Correction 2024 | BTC | -8.96% | 10.01% | **-0.89** | -11.0% | 31.28% |
| Correction 2024 | ETH | -11.05% | 11.67% | **-0.95** | -28.3% | 45.32% |
| Correction 2024 | SOL | -6.00% | 8.66% | **-0.69** | -24.0% | 45.06% |

In the falling windows the strategy loses less than buy & hold because it
is short. In the bull years it loses money while buy & hold compounds. A
negative return with a negative denominator is not a risk-adjusted win.

## 5. Walk-forward (corrected)

Rolling windows: 90 days to pick parameters, the next 30 days scored
out-of-sample, stepping 30 days (9 windows per symbol).

| Symbol | OOS trades | OOS avg / window | Positive windows | Fixed-parameter avg | Delta |
|---|---:|---:|---:|---:|---:|
| BTC | 82 | -0.32% | 3/9 | -0.35% | +0.03 |
| ETH | 89 | -0.06% | 4/9 | -0.60% | +0.54 |
| SOL | 79 | -0.69% | 3/9 | -1.10% | +0.41 |

Both the optimised and the fixed parameter sets lose money out of sample.
The v1.0.0 claim that "optimisation hurts" was really "the bug was
smaller with fixed parameters".

## 6. Ablations (1 month, 1h, corrected)

| Variant | BTC PF | ETH PF | SOL PF | BTC return |
|---|---:|---:|---:|---:|
| Full rule set | 1.79 | 1.52 | 4.28 | +1.47% |
| No pullback filter | 0.92 | 1.35 | 1.83 | -0.27% |
| No 4h regime filter | 1.43 | 3.18 | 5.79 | +1.45% |

The filters do not rescue the one-month window, and the one-month window is
the only positive period. Ablations cannot establish an edge the base rules
do not have.

## 7. Execution-model stress

The corrected engine no longer assumes a stop always fills at its own
price. The regression fixtures measure the difference on one identical
trade:

- A normal stop fill costs **-1.05R**. The same trade with a
  2.5-point gap through the stop costs **-2.04R** — almost twice
  the damage, from the same signal and the same stop.

- A gap through the liquidation level of a 2.6x position wipes the
  account; the engine stops at zero instead of compounding a negative
  balance.

Across every single-run window in this report, gap stops occurred
**0** times and liquidations **0**
times. They are rare in 1h crypto data, but when they happen the loss is
large enough that assuming a perfect stop fill would understate risk.

All **25** breakeven exits came from a candle that contained both
TP1 and the breakeven stop, so that count is the assumption's blast radius.

## 8. Portfolio view (corrected)

Three symbols on a 30,000 USDT account (each leg keeps its own 10k sizing).
The combined curve sums the three bar-level equity paths.

| Period | Ending equity | Return | Max DD (intrabar) | Max DD (close) | Trades |
|---|---:|---:|---:|---:|---:|
| 1 month | 30,816 | +2.7% | 0.98% | 1.00% | 32 |
| 12 months | 28,385 | -5.4% | 10.93% | 10.87% | 353 |
| Bull 2023 | 27,010 | -10.0% | 11.75% | 11.69% | 319 |
| Bull 2024 | 27,444 | -8.5% | 11.81% | 11.74% | 323 |
| Correction 2024 | 27,400 | -8.7% | 8.67% | 8.67% | 158 |

The intrabar column marks each leg at its worst adverse price with the
live stop as a floor and assumes the three extremes coincide: a stress
figure, not a forecast. The close column is the realised path. Both are
worse than adding up per-symbol drawdowns, which would ignore that losses
can arrive together.

## 9. What this backtest still does not model

| Gap | Why it matters |
|---|---|
| Survivorship bias | BTC/ETH/SOL are survivors picked today; the 2023 universe was different |
| Exact liquidation schedule | One MMR (0.5%) for all three symbols; Bybit tiers vary with size |
| Funding beyond the bar close | Funding is charged once per 8h stamp at the bar close |
| Slippage | Fixed at 2 bps; real slippage grows with size and volatility |
| Single venue | Bybit only |
| Correlation | Portfolio DD does not capture a simultaneous adverse move |
| Partial fills | The engine assumes every requested size fills |

## 10. Verdict

| Question | Answer |
|---|---|
| Is the one-month result proof of edge? | **No.** 32/32 trades were short in a falling month |
| Is there positive expectancy? | **No.** PF < 1 in 12 of 15 one-hour cells |
| Does it beat buy & hold? | **Only by losing less in falling markets.** It loses in bull years |
| Is it good risk-adjusted? | **No.** Returns are negative in most periods |
| Does optimisation help? | **No.** Both optimised and fixed parameters lose out of sample |
| Ready for live capital? | **No.** The edge was the bug |

**Honest summary:** the v1.0.0 report measured a look-ahead bug, not a
strategy. With the bug fixed the trend rules lose money in 12 of 15
one-hour cells and in all three walk-forward symbols. The infrastructure
(router, risk gates, execution, journal) is still usable, but this strategy
has no demonstrated edge and must not be traded with real money.

## 11. Reproduce

```bash
# one window
PYTHONPATH=src .venv/bin/python scripts/backtest_trend.py --mode single \
  --symbols BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT --timeframes 1h \
  --start 2025-11-14 --end 2026-11-14 --warmup-days 75

# walk-forward
PYTHONPATH=src .venv/bin/python scripts/backtest_trend.py --mode walkforward \
  --symbols BTC/USDT:USDT --start 2025-11-01 --end 2026-11-14 \
  --train-days 90 --test-days 30 --step-days 30

# regression tests for the honesty rules
.venv/bin/python -m pytest tests/test_backtest.py -q
```

Data is cached under `/tmp/cfos-backtest-cache`. Delete it to refetch.
