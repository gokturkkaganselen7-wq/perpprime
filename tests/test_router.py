import importlib.util
from pathlib import Path

import pytest

from cfos.router import GATES, INTENTS, catalogue, detect_intent, route

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"

EXPECTED_SKILLS = {
    "cfos-router",
    "cfos-risk-gate",
    "cfos-portfolio-state",
    "cfos-data-quality",
    "cfos-regime",
    "cfos-derivatives-sentiment",
    "cfos-liquidation-map",
    "cfos-macro-calendar",
    "cfos-trend",
    "cfos-breakout",
    "cfos-mean-reversion",
    "cfos-funding-carry",
    "cfos-liquidation-reversal",
    "cfos-grid",
    "cfos-news-catalyst",
    "cfos-backtest",
    "cfos-walk-forward",
    "cfos-edge-review",
    "cfos-sizing",
    "cfos-execution",
    "cfos-exit-management",
    "cfos-journal",
    "cfos-postmortem",
    "cfos-ops",
}


def test_catalogue_has_thirteen_intents():
    assert len(catalogue()) == 13
    assert set(INTENTS) == set(catalogue())


def test_every_intent_has_chain_and_note():
    for name, entry in INTENTS.items():
        assert entry["skills"], name
        assert entry["note"].strip(), name


def test_every_chain_skill_is_installed():
    for intent, chain in catalogue().items():
        for skill in chain:
            assert (SKILLS / skill / "SKILL.md").is_file(), f"{intent} -> {skill}"


def test_installed_skills_match_expected_set():
    installed = {p.parent.name for p in SKILLS.glob("*/SKILL.md")}
    assert installed == EXPECTED_SKILLS


def test_registry_audit_is_in_sync():
    script = SKILLS / "cfos-router" / "scripts" / "audit_registry.py"
    spec = importlib.util.spec_from_file_location("cfos_audit_registry", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    router_text = (SKILLS / "cfos-router" / "SKILL.md").read_text(encoding="utf-8")
    routed = set(module.registry_names(router_text))
    installed = module.installed_names(SKILLS)
    assert routed == installed == EXPECTED_SKILLS


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("BTC rejim nasıl?", "regime"),
        ("kırılım var mı", "breakout"),
        ("range breakout setup", "breakout"),
        ("likidasyon oldu, döner mi", "liquidation_reversal"),
        ("funding carry yapalım", "funding_carry"),
        ("grid kuralım", "grid"),
        ("CPI haberi ne yapar", "news"),
        ("trend takip edelim", "trend"),
        ("ortalamaya dönüş", "mean_reversion"),
        ("kaç lot alayım", "size"),
        ("son işlemleri review et", "review"),
        ("backtest çalıştır", "backtest"),
        ("hedge lazım", "hedge"),
        ("bilinmeyen bir cümle", "regime"),
    ],
)
def test_detect_intent(text, expected):
    assert detect_intent(text) == expected


def test_route_by_intent_and_text():
    plan = route(intent="breakout")
    assert plan.intent == "breakout"
    assert plan.skills == INTENTS["breakout"]["skills"]
    assert plan.gates == GATES

    from_text = route(text="kırılım")
    assert from_text.intent == "breakout"


def test_unknown_intent_raises():
    with pytest.raises(KeyError):
        route(intent="yolo")


def test_gate_list_is_stable_and_complete():
    assert GATES[0] == "kill_switch"
    assert "leverage" in GATES
    assert len(GATES) == 15
