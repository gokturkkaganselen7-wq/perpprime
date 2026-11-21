# PerpPrime — Trend Backtest Report

> **CORRECTION — 2026-11-22.** The first version of this report (commit
> `3cd415d`) was wrong. The engine filled entries at the **signal bar's
> open** instead of the **next bar's open**, a one-bar look-ahead that gave
> every trade a free bar body.
>
> The bug is fixed in `scripts/backtest_trend.py`;
> `tests/test_backtest.py` locks the behaviour down. Every number below was
> re-run from scratch with the fixed engine.
>
> **The previous results are retracted. After the fix the trend rules show
> no edge: 12 of 15 one-hour period/symbol cells are negative, profit
> factors are 0.38–1.00, and 12-month drawdowns reach 15.9%. Do not trade
> this strategy with real money.**

## TL;DR

- **12 of 15** one-hour cells are negative after the fix (v1.0.0 reported
  15/15 positive).
- Profit factor is **0.38–1.00** in the losing cells; win rates fall to
  21–38%.
- The only positive window is the one-month crash (2026-10-15 →
  2026-11-14), where 133 of 134 trades were short. That is regime beta,
  not edge.
- 12-month max drawdown is **7.5% / 9.5% / 15.9%** (BTC/ETH/SOL). SOL is
  above the 10% live limit.
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

| Period | Symbol | v1.0.0 return | Corrected | v1.0.0 PF | Corrected PF | v1.0.0 DD | Corrected DD |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 month | BTC | +3.30% | **+1.47%** | 2.78 | 1.79 | 1.25% | 1.30% |
| 1 month | ETH | +7.69% | **+1.25%** | 5.20 | 1.52 | 1.23% | 1.81% |
| 1 month | SOL | +13.55% | **+4.21%** | 10.14 | 3.55 | 0.56% | 0.54% |
| 12 months | BTC | +32.54% | **-0.13%** | 1.87 | 1.00 | 3.64% | 7.51% |
| 12 months | ETH | +42.30% | **-5.44%** | 2.13 | 0.86 | 4.19% | 9.53% |
| 12 months | SOL | +42.06% | **-10.77%** | 1.96 | 0.72 | 3.45% | 15.89% |
| Bull 2023 | BTC | +20.01% | **-6.45%** | 1.55 | 0.81 | 6.65% | 10.09% |
| Bull 2023 | ETH | +2.51% | **-17.46%** | 1.06 | 0.55 | 6.61% | 19.43% |
| Bull 2023 | SOL | +25.62% | **-5.54%** | 1.61 | 0.84 | 2.85% | 9.81% |
| Bull 2024 | BTC | +31.37% | **-9.31%** | 1.93 | 0.73 | 3.73% | 14.09% |
| Bull 2024 | ETH | +15.60% | **-13.57%** | 1.33 | 0.61 | 5.87% | 16.49% |
| Bull 2024 | SOL | +26.19% | **-2.35%** | 1.76 | 0.93 | 3.55% | 8.43% |
| Correction 2024 | BTC | +12.43% | **-8.96%** | 1.73 | 0.54 | 3.73% | 10.01% |
| Correction 2024 | ETH | +7.05% | **-10.70%** | 1.33 | 0.38 | 3.57% | 11.28% |
| Correction 2024 | SOL | +9.55% | **-6.00%** | 1.53 | 0.66 | 2.68% | 8.43% |

Every walk-forward and ablation result in v1.0.0 was produced by the same
buggy engine, so those conclusions are retracted as well.

## 3. Results (corrected)

### 3.1 1 month (2026-10-15 → 2026-11-14)

**1h**

| Symbol | Trades | Long/Short | Win | Avg R | PF | Max DD | Return | Fees | Funding | Buy & hold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 9 | 0/9 | 55.6% | +0.33 | 1.79 | 1.30% | **+1.47%** | 54.6 | 9.4 | -16.3% |
| ETH | 10 | 0/10 | 50.0% | +0.25 | 1.52 | 1.81% | **+1.25%** | 51.0 | 4.6 | -20.2% |
| SOL | 12 | 0/12 | 75.0% | +0.69 | 3.55 | 0.54% | **+4.21%** | 45.9 | 5.2 | -25.5% |
| **Total** | **31** | | **61.3%** | | | | **avg 2.31%** | **152** | **19** | |

**15m**

| Symbol | Trades | Long/Short | Win | Avg R | PF | Max DD | Return | Fees | Funding | Buy & hold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 20 | 1/19 | 45.0% | +0.03 | 1.04 | 2.38% | **+0.24%** | 212.7 | 9.5 | -16.3% |
| ETH | 41 | 0/41 | 41.5% | -0.03 | 0.95 | 5.95% | **-0.73%** | 372.9 | 7.1 | -20.2% |
| SOL | 42 | 0/42 | 42.9% | +0.22 | 1.34 | 2.23% | **+4.50%** | 316.4 | 9.7 | -25.5% |
| **Total** | **103** | | **42.7%** | | | | **avg 1.34%** | **902** | **26** | |

### 3.2 12 months (2025-11-14 → 2026-11-14)

**1h**

| Symbol | Trades | Long/Short | Win | Avg R | PF | Max DD | Return | Fees | Funding | Buy & hold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 107 | 40/67 | 35.5% | +0.00 | 1.00 | 7.51% | **-0.13%** | 622.0 | 12.0 | -31.4% |
| ETH | 122 | 50/72 | 34.4% | -0.09 | 0.86 | 9.53% | **-5.44%** | 510.5 | -0.6 | -40.2% |
| SOL | 121 | 47/74 | 32.2% | -0.18 | 0.72 | 15.89% | **-10.77%** | 426.7 | -3.0 | -46.3% |
| **Total** | **350** | | **34.0%** | | | | **avg -5.45%** | **1559** | **9** | |

### 3.3 Bull 2023 (2023-01-01 → 2024-01-01)

**1h**

| Symbol | Trades | Long/Short | Win | Avg R | PF | Max DD | Return | Fees | Funding | Buy & hold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 102 | 77/25 | 38.2% | -0.13 | 0.81 | 10.09% | **-6.45%** | 591.5 | -56.2 | +156.9% |
| ETH | 107 | 64/43 | 25.2% | -0.35 | 0.55 | 19.43% | **-17.46%** | 578.6 | -7.9 | +92.0% |
| SOL | 109 | 68/41 | 33.0% | -0.10 | 0.84 | 9.81% | **-5.54%** | 270.8 | -15.2 | +924.4% |
| **Total** | **318** | | **32.1%** | | | | **avg -9.81%** | **1441** | **-79** | |

### 3.4 Bull 2024 (2024-01-01 → 2025-01-01)

**1h**

| Symbol | Trades | Long/Short | Win | Avg R | PF | Max DD | Return | Fees | Funding | Buy & hold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 107 | 76/31 | 34.6% | -0.18 | 0.73 | 14.09% | **-9.31%** | 451.1 | -92.0 | +122.9% |
| ETH | 110 | 58/52 | 30.0% | -0.26 | 0.61 | 16.49% | **-13.57%** | 362.9 | -54.9 | +47.2% |
| SOL | 102 | 63/39 | 33.3% | -0.04 | 0.93 | 8.43% | **-2.35%** | 238.5 | -64.8 | +88.9% |
| **Total** | **319** | | **32.6%** | | | | **avg -8.41%** | **1053** | **-212** | |

### 3.5 Correction 2024 (2024-04-01 → 2024-10-01)

**1h**

| Symbol | Trades | Long/Short | Win | Avg R | PF | Max DD | Return | Fees | Funding | Buy & hold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 53 | 26/27 | 26.4% | -0.35 | 0.54 | 10.01% | **-8.96%** | 229.6 | -8.6 | -11.0% |
| ETH | 51 | 16/35 | 21.6% | -0.44 | 0.38 | 11.28% | **-10.70%** | 158.5 | -0.7 | -28.3% |
| SOL | 50 | 19/31 | 24.0% | -0.24 | 0.66 | 8.43% | **-6.00%** | 130.7 | -7.5 | -24.0% |
| **Total** | **154** | | **24.0%** | | | | **avg -8.55%** | **519** | **-17** | |

## 4. Strategy vs buy & hold

| Period | Symbol | Strategy | Strategy DD | Strategy ret/DD | Buy & hold | B&H DD |
|---|---|---:|---:|---:|---:|---:|
| 1 month | BTC | +1.47% | 1.30% | **1.13** | -16.3% | 19.70% |
| 1 month | ETH | +1.25% | 1.81% | **0.69** | -20.2% | 23.99% |
| 1 month | SOL | +4.21% | 0.54% | **7.80** | -25.5% | 28.49% |
| 12 months | BTC | -0.13% | 7.51% | **-0.02** | -31.4% | 41.49% |
| 12 months | ETH | -5.44% | 9.53% | **-0.57** | -40.2% | 55.12% |
| 12 months | SOL | -10.77% | 15.89% | **-0.68** | -46.3% | 58.77% |
| Bull 2023 | BTC | -6.45% | 10.09% | **-0.64** | +156.9% | 21.85% |
| Bull 2023 | ETH | -17.46% | 19.43% | **-0.90** | +92.0% | 28.40% |
| Bull 2023 | SOL | -5.54% | 9.81% | **-0.56** | +924.4% | 47.30% |
| Bull 2024 | BTC | -9.31% | 14.09% | **-0.66** | +122.9% | 32.41% |
| Bull 2024 | ETH | -13.57% | 16.49% | **-0.82** | +47.2% | 46.74% |
| Bull 2024 | SOL | -2.35% | 8.43% | **-0.28** | +88.9% | 46.61% |
| Correction 2024 | BTC | -8.96% | 10.01% | **-0.90** | -11.0% | 31.28% |
| Correction 2024 | ETH | -10.70% | 11.28% | **-0.95** | -28.3% | 45.32% |
| Correction 2024 | SOL | -6.00% | 8.43% | **-0.71** | -24.0% | 45.06% |

In the falling windows the strategy loses less than buy & hold because it
is short. In the bull years it loses money while buy & hold compounds. A
negative return with a negative denominator is not a risk-adjusted win.

## 5. Walk-forward (corrected)

Rolling windows: 90 days to pick parameters, the next 30 days scored
out-of-sample, stepping 30 days (9 windows per symbol).

| Symbol | OOS trades | OOS avg / window | Positive windows | Fixed-parameter avg | Delta |
|---|---:|---:|---:|---:|---:|
| BTC | 82 | -0.48% | 3/9 | -0.48% | +0.00 |
| ETH | 88 | -0.29% | 4/9 | -0.62% | +0.33 |
| SOL | 83 | -1.03% | 3/9 | -1.04% | +0.01 |

Both the optimised and the fixed parameter sets lose money out of sample.
The v1.0.0 claim that "optimisation hurts" was really "the bug was
smaller with fixed parameters".

## 6. Ablations (1 month, 1h, corrected)

| Variant | BTC PF | ETH PF | SOL PF | BTC return |
|---|---:|---:|---:|---:|
| Full rule set | 1.79 | 1.52 | 3.55 | +1.47% |
| No pullback filter | 0.92 | 1.35 | 1.91 | -0.27% |
| No 4h regime filter | 1.43 | 3.18 | 4.89 | +1.45% |

The filters do not rescue the one-month window, and the one-month window is
the only positive period. Ablations cannot establish an edge the base rules
do not have.

## 7. Portfolio view (corrected)

Three symbols on a 30,000 USDT account (each leg keeps its own 10k sizing,
PnL summed by exit time).

| Period | Ending equity | Return | Combined max DD | Trades |
|---|---:|---:|---:|---:|
| 1 month | 30,693 | +2.3% | 0.98% | 31 |
| 12 months | 28,366 | -5.4% | 10.31% | 350 |
| Bull 2023 | 27,056 | -9.8% | 11.30% | 318 |
| Bull 2024 | 27,477 | -8.4% | 11.56% | 319 |
| Correction 2024 | 27,435 | -8.6% | 8.56% | 154 |

## 8. What this backtest does not model

| Gap | Why it matters |
|---|---|
| Survivorship bias | BTC/ETH/SOL are survivors picked today; the 2023 universe was different |
| Liquidation | Not modelled; the 3x cap and distant stops are assumed sufficient |
| Gap risk on stops | Stops are assumed to fill exactly at the stop price |
| Intrabar drawdown | Max DD is measured on closed-trade equity, so it understates the worst moment |
| TP1 + breakeven in one bar | After TP1 the same bar is checked for breakeven; the OHLC bar cannot prove the order |
| Slippage | Fixed at 2 bps; real slippage grows with size and volatility |
| Single venue | Bybit only |
| Correlation | Portfolio DD does not capture a simultaneous adverse move |
| Funding | Taken from real history, but priced once per 8h stamp at the bar close |

## 9. Verdict

| Question | Answer |
|---|---|
| Is the one-month result proof of edge? | **No.** 133/134 trades were short in a 16–25% falling month |
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

## 10. Reproduce

```bash
# one window
PYTHONPATH=src .venv/bin/python scripts/backtest_trend.py --mode single \
  --symbols BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT --timeframes 1h \
  --start 2025-11-14 --end 2026-11-14 --warmup-days 75

# walk-forward
PYTHONPATH=src .venv/bin/python scripts/backtest_trend.py --mode walkforward \
  --symbols BTC/USDT:USDT --start 2025-11-01 --end 2026-11-14 \
  --train-days 90 --test-days 30 --step-days 30

# regression test for the entry-fill rule
.venv/bin/python -m pytest tests/test_backtest.py -q
```

Data is cached under `/tmp/cfos-backtest-cache`. Delete it to refetch.
