"""Testes unitários do parser de decklists."""

from __future__ import annotations

from services.decklist_parse import parse_card_line, parse_decklist_text, parse_pasted_decklist


class TestParseCardLine:
    def test_basic_qty(self):
        assert parse_card_line("1x Sol Ring") == (1, "Sol Ring")

    def test_uppercase_x_and_tags(self):
        assert parse_card_line("2X Path to Exile (cmm) [Removal]") == (2, "Path to Exile")

    def test_invalid(self):
        assert parse_card_line("") is None
        assert parse_card_line("# Ramp") is None
        assert parse_card_line("Sol Ring") is None


class TestParseDecklistText:
    def test_blocks_and_prose(self):
        text = (
            "Aqui vai o pacote de ramp:\n"
            "\n"
            "# Ramp\n"
            "1x Sol Ring\n"
            "1x Arcane Signet\n"
            "\n"
            "Depois olhamos draw.\n"
            "# Draw\n"
            "1x Rhystic Study\n"
        )
        parsed = parse_decklist_text(text)
        assert parsed.has_structured_list
        assert "pacote de ramp" in parsed.prose_before
        assert [b.title for b in parsed.blocks] == ["Ramp", "Draw"]
        assert parsed.blocks[0].cards == [(1, "Sol Ring"), (1, "Arcane Signet")]
        assert parsed.blocks[1].cards == [(1, "Rhystic Study")]

    def test_blank_line_ends_block(self):
        text = "# Lands\n1x Island\n\ntexto depois\n"
        parsed = parse_decklist_text(text)
        assert len(parsed.blocks) == 1
        assert parsed.blocks[0].cards == [(1, "Island")]
        assert "texto depois" in parsed.prose_after

    def test_empty_header_block_discarded(self):
        text = "# Empty\n\n# Real\n1x Forest\n"
        parsed = parse_decklist_text(text)
        assert [b.title for b in parsed.blocks] == ["Real"]


class TestParsePastedDecklist:
    def test_mixed_formats(self):
        text = "# Ignore\n1x Sol Ring\nCounterspell\n\n2x Negate\n"
        assert parse_pasted_decklist(text) == [
            (1, "Sol Ring"),
            (1, "Counterspell"),
            (2, "Negate"),
        ]
