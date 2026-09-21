"""Red-flag emergency detector — escalates on emergency indicators, non-blocking."""

from __future__ import annotations

import pytest

from app.guardrails.redflag import detect_red_flags


@pytest.mark.parametrize(
    ("query", "category"),
    [
        ("crushing chest pain radiating to the left arm", "cardiac"),
        ("management of anaphylaxis in adults", "anaphylaxis"),
        ("sudden weakness and slurred speech", "stroke"),
        ("patient reports suicidal ideation", "suicidal"),
        ("septic shock with hypotension and elevated lactate", "sepsis"),
        ("patient in cardiac arrest with no pulse", "airway_breathing"),
    ],
)
def test_emergencies_trigger(query: str, category: str) -> None:
    result = detect_red_flags(query)
    assert result.triggered
    assert category in result.categories
    assert result.banner is not None


@pytest.mark.parametrize(
    "query",
    [
        "first-line treatment for type 2 diabetes",
        "evidence for SGLT2 inhibitors in heart failure",
        "recommended statin for primary prevention",
    ],
)
def test_routine_queries_do_not_trigger(query: str) -> None:
    assert not detect_red_flags(query).triggered


def test_banner_has_emergency_guidance() -> None:
    banner = detect_red_flags("anaphylaxis").banner
    assert banner is not None
    assert "emergency" in banner.lower()
    assert "911" in banner or "112" in banner
