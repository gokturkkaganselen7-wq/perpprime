"""Tests for the regime-aware allocator (the fail-closed "brain")."""

from cfos.allocator import (
    CANDIDATE,
    CONFIRMED,
    NO_EDGE,
    StrategyRecord,
    allocate,
    load_registry,
)


def rec(name="s", regimes=("range",), status=CONFIRMED, **over):
    base = dict(name=name, skill=f"cfos-{name}", regimes=regimes,
                status=status, chain=(f"cfos-{name}", "cfos-sizing"),
                oos_avg=5.0, positive_windows=8, windows=9, pf=1.6,
                max_dd=5.0, oos_trades=220)
    base.update(over)
    return StrategyRecord(**base)


def test_registry_loads_every_measured_strategy():
    records = load_registry()
    names = {r.name for r in records}
    assert "trend" in names
    assert "basis_carry" in names
    assert "funding_dispersion" in names
    assert all(r.status != CONFIRMED for r in records)


def test_kill_switch_blocks_everything():
    alloc = allocate("range", [rec()], kill_engaged=True)
    assert alloc.decision == "NO_TRADE"
    assert "kill switch" in alloc.reasons[0]


def test_chaos_regime_blocks_everything():
    alloc = allocate("chaos", [rec(regimes=("chaos",))])
    assert alloc.decision == "NO_TRADE"
    assert "chaos" in alloc.reasons[0]


def test_no_confirmed_edge_means_no_trade():
    alloc = allocate("range", [rec(status=NO_EDGE)])
    assert alloc.decision == "NO_TRADE"
    assert alloc.rejected[0]["strategy"] == "s"


def test_confirmed_edge_in_regime_is_selected():
    alloc = allocate("range", [rec()])
    assert alloc.decision == "TRADE"
    assert alloc.strategy == "s"
    assert alloc.chain == ["cfos-s", "cfos-sizing"]


def test_candidate_only_trades_on_the_paper_flag():
    blocked = allocate("range", [rec(status=CANDIDATE)])
    assert blocked.decision == "NO_TRADE"
    paper = allocate("range", [rec(status=CANDIDATE)], allow_candidate=True)
    assert paper.decision == "PAPER"
    assert paper.strategy == "s"


def test_best_candidate_wins_by_return_per_drawdown():
    weak = rec(name="weak", oos_avg=2.0, max_dd=4.0)       # 0.5
    strong = rec(name="strong", oos_avg=6.0, max_dd=3.0)   # 2.0
    alloc = allocate("range", [weak, strong])
    assert alloc.strategy == "strong"
