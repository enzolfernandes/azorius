"""Testes do template de composição Commander."""

from __future__ import annotations

from services.deck_composition import (
    LAND_SLOTS_MAX,
    LAND_SLOTS_MIN,
    PACKAGE_RANGES,
    format_template_for_prompt,
    gaps_from_counts,
    mid_target,
    package_mins,
    spell_role_mins,
)
from services.llm_engine import (
    DECKBUILDER_SYSTEM_PROMPT,
    DECKBUILDER_UPGRADE_SYSTEM_PROMPT,
)


def _sum_bounds() -> tuple[int, int]:
    mins = sum(lo for lo, _ in PACKAGE_RANGES.values())
    maxs = sum(hi for _, hi in PACKAGE_RANGES.values())
    return mins, maxs


def test_land_slots_match_template():
    assert PACKAGE_RANGES["lands"] == (35, 38)
    assert LAND_SLOTS_MIN == 35
    assert LAND_SLOTS_MAX == 38


def test_ranges_span_around_99():
    mins, maxs = _sum_bounds()
    assert mins <= 99 <= maxs
    assert mins == 93  # 35+10+10+10+3+25
    assert maxs == 106  # 38+12+10+12+4+30


def test_mid_and_mins():
    assert mid_target("lands") == 36
    assert mid_target("draw") == 10
    assert package_mins()["board_wipe"] == 3
    roles = spell_role_mins()
    assert roles["ramp"] == 10
    assert roles["synergy"] == 25
    assert "lands" not in roles


def test_gaps_below_min():
    gaps = gaps_from_counts(
        {
            "lands": 30,
            "ramp": 5,
            "draw": 10,
            "removal": 12,
            "board_wipe": 4,
            "synergy": 30,
        }
    )
    by_pkg = {g["package"]: g for g in gaps}
    assert "lands" in by_pkg
    assert by_pkg["lands"]["deficit"] == 5
    assert by_pkg["lands"]["target_label"] == "35–38"
    assert "ramp" in by_pkg
    assert by_pkg["ramp"]["deficit"] == 5
    assert "draw" not in by_pkg


def test_gaps_synergy_counts_aliases():
    gaps = gaps_from_counts(
        {
            "lands": 36,
            "ramp": 11,
            "draw": 10,
            "removal": 11,
            "board_wipe": 3,
            "other": 20,
            "protection": 5,
        }
    )
    assert not any(g["package"] == "synergy" for g in gaps)


def test_gaps_none_when_all_at_min():
    counts = {pkg: lo for pkg, (lo, _) in PACKAGE_RANGES.items()}
    assert gaps_from_counts(counts) == []


def test_prompt_template_contains_ranges():
    text = format_template_for_prompt()
    assert "35–38" in text
    assert "10–12" in text
    assert "Board Wipes" in text
    assert "Ramp" in text
    assert "35–38" in DECKBUILDER_SYSTEM_PROMPT
    assert "Passo 6" in DECKBUILDER_SYSTEM_PROMPT
    assert "TEMPLATE DE COMPOSIÇÃO" in DECKBUILDER_UPGRADE_SYSTEM_PROMPT
