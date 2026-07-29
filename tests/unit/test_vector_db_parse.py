"""Testes unitários do chunking das Comprehensive Rules."""

from __future__ import annotations

from services.vector_db import parse_rules_chunks


def test_groups_subrules_with_parent(sample_rules_text):
    chunks = parse_rules_chunks(sample_rules_text)
    by_num = {c["rule_number"]: c["text"] for c in chunks}
    assert "100.1" in by_num
    assert "100.1a" in by_num["100.1"]
    assert "100.1b" in by_num["100.1"]
    assert "601.2" in by_num
    assert "601.2a" in by_num["601.2"]


def test_stops_at_glossary(sample_rules_text):
    chunks = parse_rules_chunks(sample_rules_text)
    joined = "\n".join(c["text"] for c in chunks)
    assert "Glossary" not in joined
    assert "Ability —" not in joined


def test_empty_input():
    assert parse_rules_chunks("") == []
