"""Regression tests for the cross-sectional momentum engine."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from backtest_xsec import XsecParams, simulate_xsec  # noqa: E402

HOUR = 3_600_000


def _uni(series, atr=1.0, funding=None):
    """Build a fake universe from {symbol: [close, ...]}."""
    n = len(next(iter(series.values())))
    index = [i * HOUR for i in range(n)]
    data = {}
    for sym, closes in series.items():
        rows = [[i * HOUR, c, c, c, c, 1.0] for i, c in enumerate(closes)]
        data[sym] = {
            "rows": rows,
            "by_ts": {int(r[0]): r for r in rows},
            "atr": [atr] * n,
            "funding": (funding or {}).get(sym, [0.0] * n),
        }
    return {"data": data, "index": index}


def test_ranks_long_the_winner_and_short_the_loser():
    n = 200
    winners = [100.0 + i * 0.1 for i in range(n)]     # +20% over the window
    flat = [100.0] * n
    losers = [100.0 - i * 0.1 for i in range(n)]

    uni = _uni({"AAA/USDT:USDT": winners, "BBB/USDT:USDT": flat,
                "CCC/USDT:USDT": losers})
    params = XsecParams(lookback_bars=24, rebalance_bars=24, k=1, stop_atr=1.5)

    res = simulate_xsec(uni, params, 0, n * HOUR)

    sides = {(t["symbol"], t["side"]) for t in res["trades_detail"]}
    assert ("AAA/USDT:USDT", "long") in sides
    assert ("CCC/USDT:USDT", "short") in sides
    assert ("BBB/USDT:USDT", "long") not in sides
    assert ("BBB/USDT:USDT", "short") not in sides


def test_a_persistent_rank_does_not_churn():
    """If the ranking never changes, the engine should hold two positions."""
    n = 200
    winners = [100.0 + i * 0.1 for i in range(n)]
    flat = [100.0] * n
    losers = [100.0 - i * 0.1 for i in range(n)]

    uni = _uni({"AAA/USDT:USDT": winners, "BBB/USDT:USDT": flat,
                "CCC/USDT:USDT": losers})
    params = XsecParams(lookback_bars=24, rebalance_bars=24, k=1, stop_atr=1.5)

    res = simulate_xsec(uni, params, 0, n * HOUR)

    # Two legs opened once and closed at the window edge; no rebalance churn.
    assert res["trades"] == 2
    assert all(t["exit_reason"] == "window_end" for t in res["trades_detail"])


def test_no_trades_before_the_lookback_is_available():
    n = 20
    uni = _uni({"AAA/USDT:USDT": [100.0] * n, "BBB/USDT:USDT": [100.0] * n,
                "CCC/USDT:USDT": [100.0] * n})
    params = XsecParams(lookback_bars=168, rebalance_bars=72, k=1)

    res = simulate_xsec(uni, params, 0, n * HOUR)

    assert res["trades"] == 0
    assert res["total_return_pct"] == 0.0


def test_funding_reaches_equity_for_a_long_leg():
    """A long paying positive funding must show up in equity, not just the log."""
    n = 200
    winners = [100.0 + i * 0.1 for i in range(n)]
    flat = [100.0] * n
    losers = [100.0 - i * 0.1 for i in range(n)]
    series = {"AAA/USDT:USDT": winners, "BBB/USDT:USDT": flat,
              "CCC/USDT:USDT": losers}
    rates = [0.0] * n
    for i in range(0, n, 8):
        rates[i] = 0.001          # the long leg pays

    params = XsecParams(lookback_bars=24, rebalance_bars=24, k=1, stop_atr=1.5)
    free = simulate_xsec(_uni(series), params, 0, n * HOUR)
    paid = simulate_xsec(_uni(series, funding={"AAA/USDT:USDT": rates}),
                         params, 0, n * HOUR)

    assert paid["trades"] == free["trades"] == 2
    assert paid["final_equity"] < free["final_equity"]
    long_trade = [t for t in paid["trades_detail"]
                  if t["symbol"] == "AAA/USDT:USDT"][0]
    assert long_trade["funding"] < 0
