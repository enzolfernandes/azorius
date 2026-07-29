"""Testes unitários de services.config (validação e preços BRL)."""

from __future__ import annotations

import json

import pytest

from services.config import (
    USD_BRL_RATE,
    ConfigError,
    estimate_brl_from_usd,
    load_ui_defaults,
    resolve_brl_price,
    save_ui_settings,
    settings_from_values,
)


class TestSettingsFromValues:
    def test_accepts_valid_provider(self):
        s = settings_from_values("Gemini", "  key-123  ")
        assert s.llm_provider == "gemini"
        assert s.api_key == "key-123"

    def test_rejects_unknown_provider(self):
        with pytest.raises(ConfigError, match="Provedor inválido"):
            settings_from_values("llama", "key")

    def test_rejects_empty_key(self):
        with pytest.raises(ConfigError, match="Chave de API"):
            settings_from_values("openai", "   ")


class TestEstimateBrlFromUsd:
    def test_none_and_invalid(self):
        assert estimate_brl_from_usd(None) is None
        assert estimate_brl_from_usd("abc") is None  # type: ignore[arg-type]
        assert estimate_brl_from_usd(-1.0) is None

    def test_converts_with_fixed_rate(self):
        assert estimate_brl_from_usd(2.0) == round(2.0 * USD_BRL_RATE, 2)


class TestResolveBrlPrice:
    def test_ligamagic_wins_over_usd(self):
        brl, source = resolve_brl_price(ligamagic_brl=3.5, usd=100.0)
        assert brl == 3.5
        assert source == "ligamagic"

    def test_estimated_when_ligamagic_missing(self):
        brl, source = resolve_brl_price(ligamagic_brl=None, usd=2.0)
        assert source == "estimated"
        assert brl == round(2.0 * USD_BRL_RATE, 2)

    def test_invalid_ligamagic_falls_back(self):
        brl, source = resolve_brl_price(ligamagic_brl=0, usd=1.0)
        assert source == "estimated"
        assert brl == round(1.0 * USD_BRL_RATE, 2)

    def test_both_missing(self):
        assert resolve_brl_price(ligamagic_brl=None, usd=None) == (None, None)


class TestUiSettingsPersistence:
    def test_save_and_load_prefers_json(self, isolated_data_dir, monkeypatch):
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        path = save_ui_settings("openai", "sk-test")
        assert path.is_file()
        provider, key = load_ui_defaults()
        assert provider == "openai"
        assert key == "sk-test"

    def test_preserves_other_provider_keys(self, isolated_data_dir):
        save_ui_settings("gemini", "gem-key")
        save_ui_settings("openai", "oai-key")
        data = json.loads(
            (isolated_data_dir / "ui_settings.json").read_text(encoding="utf-8")
        )
        assert data["api_keys"]["gemini"] == "gem-key"
        assert data["api_keys"]["openai"] == "oai-key"
        assert data["llm_provider"] == "openai"

    def test_corrupt_json_falls_back_to_env(self, isolated_data_dir, monkeypatch):
        settings_file = isolated_data_dir / "ui_settings.json"
        settings_file.write_text("{not-json", encoding="utf-8")
        monkeypatch.setenv("LLM_PROVIDER", "claude")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anth-key")
        provider, key = load_ui_defaults()
        assert provider == "claude"
        assert key == "anth-key"
