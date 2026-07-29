"""Fixtures compartilhadas — sempre isolam I/O em tmp_path."""

from __future__ import annotations

import pytest


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    """Redireciona DATA_DIR e caminhos derivados para um diretório temporário."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr("services.config.DATA_DIR", data)
    monkeypatch.setattr("services.config.RULES_FILE", data / "MagicCompRules.txt")
    monkeypatch.setattr("services.config.UI_SETTINGS_FILE", data / "ui_settings.json")
    monkeypatch.setattr("services.config.CHROMA_DIR", data / "chroma")
    monkeypatch.setattr("services.conversations.CONVERSATIONS_DIR", data / "conversations")
    monkeypatch.setattr(
        "services.ligamagic_prices.CACHE_DIR", data / "cache" / "ligamagic"
    )
    return data


@pytest.fixture
def sample_rules_text() -> str:
    return (
        "100.1. These Magic rules apply to any Magic game.\n"
        "100.1a A player owns a card if it is in their deck.\n"
        "100.1b Tokens are owned by the player who controlled them.\n"
        "\n"
        "601.2. To cast a spell is to take it from where it is.\n"
        "601.2a To propose the casting of a spell, a player first moves it.\n"
        "\n"
        "Glossary\n"
        "Ability — An ability is...\n"
    )
