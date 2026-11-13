"""Configuration loading. TOML only, standard library only.

Resolution order for the workspace root:
  1. CFOS_HOME
  2. an explicit ``home`` argument
  3. the current working directory, if it contains ``config/risk.toml``
  4. two levels above this package (the repository checkout)
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import Mode


class ConfigError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def find_home(home: str | Path | None = None) -> Path:
    candidates: list[Path] = []
    if home:
        candidates.append(Path(home))
    if _env("CFOS_HOME"):
        candidates.append(Path(_env("CFOS_HOME")))
    candidates.append(Path.cwd())
    candidates.append(Path(__file__).resolve().parents[2])
    for candidate in candidates:
        candidate = candidate.expanduser().resolve()
        if (candidate / "config" / "risk.toml").is_file():
            return candidate
    raise ConfigError(
        "config/risk.toml not found; set CFOS_HOME or run from the cfos workspace"
    )


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"missing config file: {path}")
    with path.open("rb") as handle:
        return tomllib.load(handle)


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade_pct: float = 0.5
    daily_loss_limit_pct: float = 2.0
    weekly_loss_limit_pct: float = 5.0
    max_drawdown_pct: float = 10.0
    max_leverage: float = 3.0
    max_portfolio_leverage: float = 3.0
    max_open_positions: int = 3
    max_per_symbol: int = 1
    max_portfolio_heat_pct: float = 3.0
    max_cluster_positions: int = 2
    max_trades_per_day: int = 6
    consecutive_loss_cooldown: int = 2
    cooldown_minutes: int = 60
    min_rr: float = 2.0
    taker_fee_pct: float = 0.055
    paper_slippage_bps: float = 2.0
    max_spread_bps: float = 8.0
    max_data_age_sec: int = 120
    post_only_entries: bool = False
    max_order_retries: int = 2
    maintenance_margin_rate: float = 0.005


@dataclass(frozen=True)
class UniverseConfig:
    allow: tuple[str, ...] = ()
    clusters: dict[str, tuple[str, ...]] = field(default_factory=dict)
    min_quote_volume_usd: float = 50_000_000.0

    def cluster_of(self, symbol: str) -> str | None:
        for name, members in self.clusters.items():
            if symbol in members:
                return name
        return None


@dataclass(frozen=True)
class VenueConfig:
    name: str = "bybit"
    default_type: str = "swap"
    sandbox: bool = True
    position_idx: int = 0


@dataclass(frozen=True)
class Settings:
    home: Path
    data_dir: Path
    mode: Mode
    venue: VenueConfig
    equity: float
    risk: RiskConfig
    universe: UniverseConfig
    kill_file: Path
    db_path: Path
    api_key: str = ""
    api_secret: str = ""
    api_password: str = ""
    live_ack: str = ""

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_live(self) -> bool:
        return self.mode is Mode.LIVE


def load_settings(home: str | Path | None = None) -> Settings:
    root = find_home(home)
    risk_raw = _read_toml(root / "config" / "risk.toml")
    uni_raw = _read_toml(root / "config" / "universe.toml")

    account = risk_raw.get("account", {})
    leverage = risk_raw.get("leverage", {})
    positions = risk_raw.get("positions", {})
    discipline = risk_raw.get("discipline", {})
    execution = risk_raw.get("execution", {})
    maintenance = risk_raw.get("maintenance", {})

    risk = RiskConfig(
        risk_per_trade_pct=float(account.get("risk_per_trade_pct", 0.5)),
        daily_loss_limit_pct=float(account.get("daily_loss_limit_pct", 2.0)),
        weekly_loss_limit_pct=float(account.get("weekly_loss_limit_pct", 5.0)),
        max_drawdown_pct=float(account.get("max_drawdown_pct", 10.0)),
        max_leverage=float(leverage.get("max_leverage", 3.0)),
        max_portfolio_leverage=float(leverage.get("max_portfolio_leverage", 3.0)),
        max_open_positions=int(positions.get("max_open_positions", 3)),
        max_per_symbol=int(positions.get("max_per_symbol", 1)),
        max_portfolio_heat_pct=float(positions.get("max_portfolio_heat_pct", 3.0)),
        max_cluster_positions=int(positions.get("max_cluster_positions", 2)),
        max_trades_per_day=int(positions.get("max_trades_per_day", 6)),
        consecutive_loss_cooldown=int(discipline.get("consecutive_loss_cooldown", 2)),
        cooldown_minutes=int(discipline.get("cooldown_minutes", 60)),
        min_rr=float(discipline.get("min_rr", 2.0)),
        taker_fee_pct=float(execution.get("taker_fee_pct", 0.055)),
        paper_slippage_bps=float(execution.get("paper_slippage_bps", 2.0)),
        max_spread_bps=float(execution.get("max_spread_bps", 8.0)),
        max_data_age_sec=int(execution.get("max_data_age_sec", 120)),
        post_only_entries=bool(execution.get("post_only_entries", False)),
        max_order_retries=int(execution.get("max_order_retries", 2)),
        maintenance_margin_rate=float(maintenance.get("maintenance_margin_rate", 0.005)),
    )

    universe_raw = uni_raw.get("universe", {})
    clusters_raw = uni_raw.get("clusters", {})
    universe = UniverseConfig(
        allow=tuple(universe_raw.get("allow", [])),
        clusters={k: tuple(v) for k, v in clusters_raw.items()},
        min_quote_volume_usd=float(
            universe_raw.get("min_quote_volume_usd", 50_000_000.0)
        ),
    )

    venues_raw = uni_raw.get("venues", {})
    venue_name = _env("CFOS_VENUE") or venues_raw.get("default", "bybit")
    venue_block = venues_raw.get(venue_name, {})
    venue = VenueConfig(
        name=venue_name,
        default_type=str(venue_block.get("default_type", "swap")),
        sandbox=bool(venue_block.get("sandbox", True)),
        position_idx=int(venue_block.get("position_idx", 0)),
    )

    mode_raw = (_env("CFOS_MODE") or "paper").lower()
    try:
        mode = Mode(mode_raw)
    except ValueError as exc:
        raise ConfigError(f"CFOS_MODE must be paper|testnet|live, got {mode_raw!r}") from exc

    data_dir = Path(_env("CFOS_DATA_DIR") or (root / "data")).expanduser().resolve()
    kill_file = Path(_env("CFOS_KILL_FILE") or (data_dir / "KILL")).expanduser().resolve()

    try:
        equity = float(_env("CFOS_EQUITY") or 10_000)
    except ValueError as exc:
        raise ConfigError("CFOS_EQUITY must be numeric") from exc

    settings = Settings(
        home=root,
        data_dir=data_dir,
        mode=mode,
        venue=venue,
        equity=equity,
        risk=risk,
        universe=universe,
        kill_file=kill_file,
        db_path=data_dir / "cfos.sqlite3",
        api_key=_env("CFOS_API_KEY"),
        api_secret=_env("CFOS_API_SECRET"),
        api_password=_env("CFOS_API_PASSWORD"),
        live_ack=_env("CFOS_LIVE_ACK"),
    )
    settings.ensure_dirs()
    return settings
