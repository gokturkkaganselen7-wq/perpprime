"""Tests for the daily time-series momentum engine."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from backtest_daily_trend import DailyParams, simulate_daily  # noqa: E402

DAY = 86_400_000


def _uni(series, atr=1.0):
    n = len(next(iter(series.values())))
    index = [i * DAY for i in range(n)]
    data = {}
    for sym, closes in series.items():
        rows = [[i * DAY, c, c, c, c, 1.0] for i, c in enumerate(closes)]
        data[sym] = {"rows": rows,
                     "by_ts": {int(r[0]): r for r in rows},
                     "atr": [atr] * n,
                     "funding": [0.0] * n}
    return {"data": data, "index": index}


def test_uptrend_is_long_and_downtrend_is_short():
    n = 400
    up = [100.0 + i * 0.5 for i in range(n)]
    down = [300.0 - i * 0.5 for i in range(n)]
    uni = _uni({"AAA/USDT:USDT": up, "BBB/USDT:USDT": down})
    params = DailyParams(fast=20, slow=50, rebalance_days=7,
                         stop_atr=50.0, max_leg_pct=50.0)

    res = simulate_daily(uni, params, 0, n * DAY)

    sides = {(t["symbol"], t["side"]) for t in res["trades_detail"]}
    assert ("AAA/USDT:USDT", "long") in sides
    assert ("BBB/USDT:USDT", "short") in sides


def test_daily_window_edge_closes_at_the_window_not_the_file_end():
    n = 200
    up = [100.0 + i * 0.5 for i in range(n)]
    down = [300.0 - i * 0.5 for i in range(n)]
    uni = _uni({"AAA/USDT:USDT": up, "BBB/USDT:USDT": down})
    params = DailyParams(fast=20, slow=50, rebalance_days=7,
                         stop_atr=50.0, max_leg_pct=50.0)

    res = simulate_daily(uni, params, 0, 150 * DAY)

    assert res["trades"] >= 1
    assert max(t["exit_ts"] for t in res["trades_detail"]) == "1970-05-31 00:00"
