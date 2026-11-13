from cfos.config import UniverseConfig
from cfos.guards import (
    LIVE_ACK_PHRASE,
    KillSwitch,
    check_live_permission,
    preflight_market,
)
from cfos.models import Mode, MarketSnapshot
from cfos.risk import symbol_in_universe


def test_kill_switch_roundtrip(tmp_path):
    switch = KillSwitch(tmp_path / "KILL")
    assert switch.engaged() == (False, None)
    switch.engage("daily loss breached")
    engaged, reason = switch.engaged()
    assert engaged and "daily loss" in reason
    assert switch.release() is True
    assert switch.engaged() == (False, None)
    assert switch.release() is False


def test_live_permission_matrix():
    assert check_live_permission(Mode.PAPER, "", False).ok
    assert check_live_permission(Mode.TESTNET, "", False).ok
    assert not check_live_permission(Mode.LIVE, "", False).ok
    assert not check_live_permission(Mode.LIVE, LIVE_ACK_PHRASE, False).ok
    assert check_live_permission(Mode.LIVE, LIVE_ACK_PHRASE, True).ok


def test_preflight_rejects_unknown_symbol_and_thin_volume():
    universe = UniverseConfig(
        allow=("BTC/USDT:USDT",), min_quote_volume_usd=10_000_000
    )
    ok, _ = preflight_market(
        MarketSnapshot(symbol="DOGE/USDT:USDT", price=1, quote_volume_24h=1e9),
        universe,
    )
    assert not ok

    ok, reason = preflight_market(
        MarketSnapshot(symbol="BTC/USDT:USDT", price=1, quote_volume_24h=1e6),
        universe,
    )
    assert not ok and "volume" in reason

    ok, _ = preflight_market(
        MarketSnapshot(symbol="BTC/USDT:USDT", price=1, quote_volume_24h=1e9),
        universe,
    )
    assert ok


def test_market_snapshot_age():
    snapshot = MarketSnapshot(symbol="BTC/USDT:USDT", price=1, ts="2026-01-01T00:00:00+00:00")
    assert snapshot.age_seconds() > 60


def test_universe_helpers():
    universe = UniverseConfig(
        allow=("BTC/USDT:USDT",), clusters={"majors": ("BTC/USDT:USDT", "ETH/USDT:USDT")}
    )
    assert universe.cluster_of("ETH/USDT:USDT") == "majors"
    assert universe.cluster_of("SOL/USDT:USDT") is None
    assert symbol_in_universe("BTC/USDT:USDT", universe)
