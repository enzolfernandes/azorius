"""Testes unitários da montagem acumulada de deck."""

from __future__ import annotations

from services.deck_assembly import (
    extract_entries_from_text,
    format_export,
    merge_entries,
    rebuild_from_messages,
    total_cards,
)


def test_extract_with_headers():
    text = "# Ramp\n1x Sol Ring\n2x Arcane Signet\n"
    assert extract_entries_from_text(text) == [
        ("Ramp", 1, "Sol Ring"),
        ("Ramp", 2, "Arcane Signet"),
    ]


def test_extract_fallback_without_headers():
    assert extract_entries_from_text("1x Sol Ring\n1x Island") == [
        ("Outros", 1, "Sol Ring"),
        ("Outros", 1, "Island"),
    ]


def test_merge_keeps_max_qty_and_latest_section():
    current = [{"name": "Sol Ring", "qty": 1, "section": "Ramp"}]
    merged = merge_entries(current, [("Staples", 1, "sol ring"), ("Draw", 1, "Rhystic Study")])
    by_name = {c["name"].lower(): c for c in merged}
    assert by_name["sol ring"]["qty"] == 1
    assert by_name["sol ring"]["section"] == "Staples"
    assert by_name["rhystic study"]["qty"] == 1


def test_merge_takes_higher_qty():
    current = [{"name": "Island", "qty": 2, "section": "Lands"}]
    merged = merge_entries(current, [("Lands", 5, "Island")])
    assert merged[0]["qty"] == 5


def test_rebuild_from_assistant_messages_only():
    messages = [
        {"role": "user", "content": "1x Ignore Me"},
        {"role": "assistant", "content": "# Ramp\n1x Sol Ring\n"},
        {"role": "assistant", "content": "# Draw\n1x Rhystic Study\n"},
    ]
    assembled = rebuild_from_messages(messages)
    assert total_cards(assembled) == 2
    export = format_export(assembled)
    assert "# Ramp" in export
    assert "1x Sol Ring" in export
    assert "# Draw" in export
    assert "1x Rhystic Study" in export
