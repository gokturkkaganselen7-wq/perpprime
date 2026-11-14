# PerpPrime — Trend Backtest Report

Backtest of the **CFOS trend rules** on real Bybit perpetual futures data.
Every number below comes from `scripts/backtest_trend.py`; nothing is estimated.

> **Not investment advice.** Leveraged perpetual futures can lose the entire
> balance. Past performance over these windows is not a forecast.

## TL;DR

- The strategy was profitable in **18 of 18** period/symbol/timeframe cells.
- It is a **capital-preserving trend follower**, not a market beater: in the
  strong bull year 2023 it returned +20.0% while BTC buy & hold returned
  +156.9%.
- Risk-adjusted (return / max drawdown) it beat buy & hold in **4 of 5**
  periods.
- Parameter optimisation made out-of-sample results **worse**, not better.
- The one-month result is **not evidence of edge**: the month fell 16–25% and
  184 of 185 trades were short.

## 1. What was tested

| Component | Rule |
|---|---|
| Venue / data | Bybit USDT perpetuals, real OHLCV (1h and 15m) |
| Regime filter | 4h CFOS regime (EMA20/50/200 + ATR + 20-bar range); only `trend_up` / `trend_down` |
| Entry trigger | Close crosses back above EMA20 after dipping below it (mirrored for shorts) |
| Stop | Last 10-bar swing ± 0.5 ATR |
| TP1 | 2R, 50% closed, stop moved to breakeven |
| TP2 | Measured move (20-bar range), clamped to 3R–8R |
| Other exits | Regime flip, 4h EMA50 break, 2-day time stop, window end |
| Costs | Taker 5.5 bps + 2 bps slippage per fill + **real Bybit funding** |
| Look-ahead | Signal on a closed bar, fill at the **next bar open** |
| Ambiguous bar | If stop and target are both inside one bar, the **stop** is assumed first |
| Warm-up | 75 days (required for 4h EMA200); never traded |
| Sizing | 0.5% equity risk per trade, 3x leverage cap |
| Starting equity | 10,000 USDT per run |

## 2. Results

### 2.1 1 month (2026-10-15 → 2026-11-14)

**1h**

| Symbol | Trades | Long/Short | Win | Avg R | PF | Max DD | Return | Fees | Funding | Buy & hold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 10 | 0/10 | 60.0% | +0.66 | 2.78 | 1.25% | **+3.30%** | 89.3 | 13.9 | -16.3% |
| ETH | 12 | 0/12 | 75.0% | +1.25 | 5.20 | 1.23% | **+7.69%** | 106.7 | 5.8 | -20.2% |
| SOL | 17 | 0/17 | 76.5% | +1.51 | 10.14 | 0.56% | **+13.55%** | 96.7 | 6.9 | -25.5% |
| **Total** | **39** | | **71.8%** | | | | **avg 8.18%** | **293** | **27** | |

**15m**

| Symbol | Trades | Long/Short | Win | Avg R | PF | Max DD | Return | Fees | Funding | Buy & hold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 30 | 1/29 | 60.0% | +0.36 | 1.75 | 1.20% | **+5.51%** | 426.7 | 14.1 | -16.3% |
| ETH | 61 | 0/61 | 50.8% | +0.32 | 1.51 | 2.43% | **+10.00%** | 761.8 | 12.5 | -20.2% |
| SOL | 55 | 0/55 | 58.2% | +0.59 | 2.27 | 1.74% | **+17.29%** | 586.4 | 13.8 | -25.5% |
| **Total** | **146** | | **55.5%** | | | | **avg 10.94%** | **1775** | **40** | |

### 2.2 12 months (2025-11-14 → 2026-11-14)

**1h**

| Symbol | Trades | Long/Short | Win | Avg R | PF | Max DD | Return | Fees | Funding | Buy & hold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 140 | 53/87 | 50.7% | +0.41 | 1.87 | 3.64% | **+32.54%** | 1187.2 | 19.9 | -31.4% |
| ETH | 145 | 58/87 | 53.8% | +0.49 | 2.13 | 4.19% | **+42.30%** | 994.6 | -0.2 | -40.2% |
| SOL | 165 | 62/103 | 50.3% | +0.43 | 1.96 | 3.45% | **+42.06%** | 1021.1 | -3.3 | -46.3% |
| **Total** | **450** | | **51.6%** | | | | **avg 38.96%** | **3203** | **16** | |

### 2.3 Bull 2023 (2023-01-01 → 2024-01-01)

**1h**

| Symbol | Trades | Long/Short | Win | Avg R | PF | Max DD | Return | Fees | Funding | Buy & hold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 124 | 91/33 | 51.6% | +0.30 | 1.55 | 6.65% | **+20.01%** | 998.9 | -88.3 | +156.9% |
| ETH | 139 | 85/54 | 43.9% | +0.04 | 1.06 | 6.61% | **+2.51%** | 1094.7 | -18.6 | +92.0% |
| SOL | 141 | 88/53 | 48.2% | +0.33 | 1.61 | 2.85% | **+25.62%** | 539.8 | -32.8 | +924.4% |
| **Total** | **404** | | **47.8%** | | | | **avg 16.05%** | **2633** | **-140** | |

### 2.4 Bull 2024 (2024-01-01 → 2025-01-01)

**1h**

| Symbol | Trades | Long/Short | Win | Avg R | PF | Max DD | Return | Fees | Funding | Buy & hold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 130 | 90/40 | 55.4% | +0.43 | 1.93 | 3.73% | **+31.37%** | 901.9 | -164.6 | +122.9% |
| ETH | 156 | 81/75 | 43.0% | +0.19 | 1.33 | 5.87% | **+15.60%** | 792.8 | -90.6 | +47.2% |
| SOL | 127 | 79/48 | 47.2% | +0.37 | 1.76 | 3.55% | **+26.19%** | 451.2 | -93.9 | +88.9% |
| **Total** | **413** | | **48.2%** | | | | **avg 24.39%** | **2146** | **-349** | |

### 2.5 Correction 2024 (2024-04-01 → 2024-10-01)

**1h**

| Symbol | Trades | Long/Short | Win | Avg R | PF | Max DD | Return | Fees | Funding | Buy & hold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | 63 | 27/36 | 50.8% | +0.38 | 1.73 | 3.73% | **+12.43%** | 380.4 | -16.9 | -11.0% |
| ETH | 73 | 23/50 | 39.7% | +0.19 | 1.33 | 3.57% | **+7.05%** | 341.2 | -5.8 | -28.3% |
| SOL | 63 | 24/39 | 42.9% | +0.30 | 1.53 | 2.68% | **+9.55%** | 230.8 | -13.1 | -24.0% |
| **Total** | **199** | | **44.2%** | | | | **avg 9.68%** | **952** | **-36** | |

## 3. Strategy vs buy & hold, risk-adjusted

Return divided by max drawdown. Higher is better.

| Period | Symbol | Strategy | Strategy DD | Strategy ret/DD | Buy & hold | B&H DD | B&H ret/DD |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 month | BTC | +3.30% | 1.25% | **2.64** | -16.3% | 19.70% | -0.83 |
| 1 month | ETH | +7.69% | 1.23% | **6.25** | -20.2% | 23.99% | -0.84 |
| 1 month | SOL | +13.55% | 0.56% | **24.20** | -25.5% | 28.49% | -0.89 |
| 12 months | BTC | +32.54% | 3.64% | **8.94** | -31.4% | 41.49% | -0.76 |
| 12 months | ETH | +42.30% | 4.19% | **10.10** | -40.2% | 55.12% | -0.73 |
| 12 months | SOL | +42.06% | 3.45% | **12.19** | -46.3% | 58.77% | -0.79 |
| Bull 2023 | BTC | +20.01% | 6.65% | **3.01** | +156.9% | 21.85% | 7.18 |
| Bull 2023 | ETH | +2.51% | 6.61% | **0.38** | +92.0% | 28.40% | 3.24 |
| Bull 2023 | SOL | +25.62% | 2.85% | **8.99** | +924.4% | 47.30% | 19.54 |
| Bull 2024 | BTC | +31.37% | 3.73% | **8.41** | +122.9% | 32.41% | 3.79 |
| Bull 2024 | ETH | +15.60% | 5.87% | **2.66** | +47.2% | 46.74% | 1.01 |
| Bull 2024 | SOL | +26.19% | 3.55% | **7.38** | +88.9% | 46.61% | 1.91 |
| Correction 2024 | BTC | +12.43% | 3.73% | **3.33** | -11.0% | 31.28% | -0.35 |
| Correction 2024 | ETH | +7.05% | 3.57% | **1.97** | -28.3% | 45.32% | -0.62 |
| Correction 2024 | SOL | +9.55% | 2.68% | **3.56** | -24.0% | 45.06% | -0.53 |

## 4. Walk-forward

Rolling windows: 90 days to pick parameters, the next 30 days scored
out-of-sample, stepping 30 days (9 windows per symbol). The grid holds 24
combinations of stop distance, TP1 multiple, pullback filter and time stop.

| Symbol | OOS trades | OOS avg / window | Positive windows | Fixed-parameter avg | Delta |
|---|---:|---:|---:|---:|---:|
| BTC | 96 | +1.53% | 6/9 | +2.45% | -0.92 |
| ETH | 117 | +2.34% | 9/9 | +3.05% | -0.71 |
| SOL | 127 | +1.97% | 6/9 | +2.24% | -0.27 |

**Optimisation hurts.** In all three symbols the fixed default parameters beat
the walk-forward-selected ones out of sample. The grid is fitting noise.

## 5. Ablations (1 month, 1h)

| Variant | BTC PF | ETH PF | SOL PF |
|---|---:|---:|---:|
| Full rule set | 2.78 | 5.20 | 10.14 |
| No pullback filter | 1.69 | 2.69 | 3.09 |
| No 4h regime filter | 2.15 | 10.26 | 24.42 |

Removing the filters does not break the result. Over this window the profit
came from being short in a falling month, not from the filters.

## 6. Portfolio view

Three symbols traded together on a 30,000 USDT account (approximation: each
leg keeps its own 10k sizing, PnL summed by exit time).

| Period | Ending equity | Return | Combined max DD |
|---|---:|---:|---:|
| 1 month | 32,454 | +8.2% | 0.59% |
| 12 months | 41,689 | +39.0% | 2.37% |
| Bull 2023 | 34,814 | +16.0% | 2.49% |
| Bull 2024 | 37,316 | +24.4% | 2.40% |
| Correction 2024 | 32,903 | +9.7% | 2.10% |

Combined drawdown is *lower* than any single symbol because losses do not
arrive on the same bar. Crypto symbols are highly correlated, so treat this
as a best case, not a guarantee.

## 7. What this backtest does not model

| Gap | Why it matters |
|---|---|
| Survivorship bias | BTC/ETH/SOL are survivors picked today; the 2023 universe was different |
| Liquidation | Not modelled; the 3x cap and distant stops are assumed sufficient |
| Gap risk on stops | Stops are assumed to fill exactly at the stop price |
| Intrabar drawdown | Max DD is measured on closed-trade equity, so it understates the worst moment |
| Slippage | Fixed at 2 bps; real slippage grows with size and volatility |
| Single venue | Bybit only |
| Correlation | Portfolio DD does not capture a simultaneous adverse move |
| Funding | Taken from real history, but priced once per 8h stamp at the bar close |
| Opportunity cost | In 2023 SOL rose 924% while the strategy made 25.6% |

## 8. Verdict

| Question | Answer |
|---|---|
| Is the one-month result proof of edge? | **No.** 1 long / 184 short, one regime, one direction |
| Is there positive expectancy? | **Yes**, PF > 1 in 18 of 18 cells |
| Does it beat buy & hold? | **No.** It lags badly in strong bull years |
| Is it good risk-adjusted? | **Yes**, in 4 of 5 periods |
| Does optimisation help? | **No.** Fixed parameters win out of sample |
| Ready for live capital? | **Not yet.** Paper-trade it first |

**Honest summary:** this is not a money printer. It is a low-drawdown trend
follower that protects capital in falling and correcting markets and
underperforms in strong bull markets. Its one-month +9.6% average is the
result of being short in a month that fell 16–25%, not evidence of skill.

## 9. Reproduce

```bash
# one window
PYTHONPATH=src .venv/bin/python scripts/backtest_trend.py --mode single \
  --symbols BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT --timeframes 1h \
  --start 2025-11-14 --end 2026-11-14 --warmup-days 75

# walk-forward
PYTHONPATH=src .venv/bin/python scripts/backtest_trend.py --mode walkforward \
  --symbols BTC/USDT:USDT --start 2025-11-01 --end 2026-11-14 \
  --train-days 90 --test-days 30 --step-days 30
```

Data is cached under `/tmp/cfos-backtest-cache`. Delete it to refetch.
