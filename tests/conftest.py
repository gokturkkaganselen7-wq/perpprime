import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cfos.config import load_settings  # noqa: E402
from cfos.models import Mode  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    base = load_settings(ROOT)
    return replace(
        base,
        data_dir=tmp_path,
        db_path=tmp_path / "cfos.sqlite3",
        kill_file=tmp_path / "KILL",
        mode=Mode.PAPER,
        equity=10_000.0,
        api_key="",
        api_secret="",
        live_ack="",
    )
