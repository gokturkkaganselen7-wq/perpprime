import pytest

from cfos.analysis import compute_regime, ema, funding_bias


def series(closes):
    """Turn a close series into OHLCV candles with a small deterministic range."""
    candles = []
    for index, close in enumerate(closes):
        spread = max(close * 0.004, 0.01)
        candles.append([index * 3_600_000, close - spread / 2, close + spread, close - spread, close, 100.0])
    return candles


def uptrend(n=220):
    return [100 * (1.004 ** i) for i in range(n)]


def downtrend(n=220):
    return [100 * (0.996 ** i) for i in range(n)]


def flat(n=220):
    return [100 + (0.2 if i % 2 else -0.2) for i in range(n)]


def test_ema_is_between_ends():
    values = [1.0] * 50 + [2.0] * 50
    result = ema(values, 20)
    assert 1.0 < result < 2.0


def test_uptrend_classification():
    report = compute_regime(series(uptrend()), symbol="BTC/USDT:USDT")
    assert report.regime == "trend_up"
    assert report.trend == "up"
    assert report.ema20 > report.ema50 > report.ema200


def test_downtrend_classification():
    report = compute_regime(series(downtrend()), symbol="BTC/USDT:USDT")
    assert report.regime == "trend_down"
    assert report.trend == "down"


def test_flat_is_range_or_squeeze():
    report = compute_regime(series(flat()), symbol="BTC/USDT:USDT")
    assert report.regime in ("range", "squeeze")


def test_chaos_on_high_atr():
    closes = [100 + (12 if i % 2 else -12) for i in range(220)]
    report = compute_regime(series(closes))
    assert report.regime == "chaos"
    assert any("ATR" in note for note in report.notes)


def test_funding_bias_labels():
    assert funding_bias(0.001) == "crowded_long"
    assert funding_bias(-0.001) == "crowded_short"
    assert funding_bias(0.0001) == "neutral"
    assert funding_bias(None) == "unknown"


def test_crowded_squeeze_note():
    report = compute_regime(series(flat()), funding_rate=0.002)
    assert report.regime == "squeeze"
    assert any("sweep" in note.lower() for note in report.notes)


def test_rejects_short_history():
    with pytest.raises(ValueError):
        compute_regime(series([100] * 10))
