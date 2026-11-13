from cfos.journal import Journal


def _open(journal, pid, symbol="BTC/USDT:USDT", risk=50.0):
    journal.open_position(
        position_id=pid,
        symbol=symbol,
        side="long",
        qty=1.0,
        entry=100.0,
        stop=95.0,
        tp=110.0,
        leverage=2.0,
        risk_amount=risk,
    )


def test_summary_math(tmp_path):
    journal = Journal(tmp_path / "j.sqlite3")
    _open(journal, "a")
    _open(journal, "b")
    _open(journal, "c")
    journal.close_position("a", exit_price=110, pnl=10.0, fees=0.1)
    journal.close_position("b", exit_price=95, pnl=-5.0, fees=0.1)
    journal.close_position("c", exit_price=98, pnl=-3.0, fees=0.1)

    summary = journal.summary()
    assert summary["trades"] == 3
    assert summary["wins"] == 1
    assert summary["losses"] == 2
    assert summary["total_pnl"] == 2.0
    assert summary["win_rate"] == 33.33
    assert summary["profit_factor"] == 1.25
    assert summary["expectancy"] == round(2.0 / 3, 4)
    assert summary["fees"] == 0.3
    assert summary["avg_r"] == round((0.2 - 0.1 - 0.06) / 3, 3)


def test_consecutive_losses_and_last_ts(tmp_path):
    journal = Journal(tmp_path / "j.sqlite3")
    for pid, pnl in (("a", 10.0), ("b", -5.0), ("c", -3.0)):
        _open(journal, pid)
        journal.close_position(pid, exit_price=100, pnl=pnl)
    count, last_ts = journal.consecutive_losses()
    assert count == 2
    assert last_ts is not None


def test_trades_today_and_realized(tmp_path):
    journal = Journal(tmp_path / "j.sqlite3")
    _open(journal, "a")
    journal.close_position("a", exit_price=100, pnl=-4.0)
    assert journal.trades_today() == 1
    assert journal.realized_today() == -4.0


def test_peak_equity_and_curve(tmp_path):
    journal = Journal(tmp_path / "j.sqlite3")
    journal.snapshot_equity(10_000)
    journal.snapshot_equity(10_400)
    journal.snapshot_equity(9_800)
    assert journal.peak_equity() == 10_400
    assert len(journal.equity_curve()) == 3


def test_decision_and_order_logging(tmp_path):
    journal = Journal(tmp_path / "j.sqlite3")
    journal.log_decision(
        mode="paper",
        verdict="BLOCKED",
        symbol="BTC/USDT:USDT",
        reasons=["kill_switch: manual"],
    )
    journal.log_order(
        client_id="x-1",
        venue="bybit",
        symbol="BTC/USDT:USDT",
        type="market",
        side="buy",
        qty=1.0,
        price=100.0,
        status="closed",
    )
    journal.log_order(
        client_id="x-1",
        venue="bybit",
        symbol="BTC/USDT:USDT",
        type="market",
        side="buy",
        qty=1.0,
        price=100.0,
        status="filled",
    )
    decisions = journal.recent_decisions()
    assert decisions[0]["verdict"] == "BLOCKED"
    row = journal._conn.execute("SELECT status FROM orders WHERE client_id='x-1'").fetchone()
    assert row["status"] == "filled"
