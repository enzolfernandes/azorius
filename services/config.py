"""Ponto único de configuração da aplicação.

Carrega o .env (fallback para uso solo) e expõe um objeto imutável de
configuração. A UI pode montar Settings a partir de valores da sessão sem
ler o ambiente diretamente — nenhum outro módulo deve chamar os.getenv.

Preferências da UI (provedor + chaves) ficam em data/ui_settings.json
(local, não versionado) e têm prioridade sobre o .env ao reabrir a app.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RULES_FILE = DATA_DIR / "MagicCompRules.txt"
UI_SETTINGS_FILE = DATA_DIR / "ui_settings.json"


def _resolve_chroma_dir() -> Path:
    """Escolhe onde persistir o ChromaDB.

    O hnswlib (motor de índice do Chroma) não grava os arquivos binários do
    índice em caminhos com caracteres não-ASCII no Windows — a escrita falha
    silenciosamente e o banco corrompe na compactação. Se o caminho do projeto
    tiver acentos (ex.: "repositórios"), usamos um diretório ASCII do usuário.
    """
    preferred = DATA_DIR / "chroma"
    try:
        str(preferred).encode("ascii")
        return preferred
    except UnicodeEncodeError:
        base = os.getenv("LOCALAPPDATA") or os.getenv("TEMP") or "."
        return Path(base) / "azorius" / "chroma"


CHROMA_DIR = _resolve_chroma_dir()

load_dotenv(PROJECT_ROOT / ".env")

VALID_PROVIDERS = ("gemini", "openai", "claude")

_PROVIDER_KEY_VARS = {
    "gemini": "GOOGLE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
}


class ConfigError(Exception):
    """Configuração ausente ou inválida."""


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    api_key: str


def _key_var_for(provider: str) -> str:
    return _PROVIDER_KEY_VARS[provider]


def settings_from_values(provider: str, api_key: str) -> Settings:
    """Valida provedor + chave e devolve Settings imutável."""
    normalized = provider.strip().lower()
    if normalized not in VALID_PROVIDERS:
        raise ConfigError(
            f"Provedor inválido: '{provider}'. Use um de: {', '.join(VALID_PROVIDERS)}."
        )
    key = api_key.strip()
    if not key:
        raise ConfigError(
            f"Chave de API não informada (obrigatória para provedor={normalized})."
        )
    return Settings(llm_provider=normalized, api_key=key)


def api_key_from_env(provider: str) -> str:
    """Lê do .env a chave correspondente ao provedor (pode ser string vazia)."""
    normalized = provider.strip().lower()
    if normalized not in VALID_PROVIDERS:
        return ""
    return os.getenv(_key_var_for(normalized), "").strip()


def env_defaults() -> tuple[str, str]:
    """Retorna (provedor, chave) sugeridos pelo .env para pré-preencher a UI."""
    provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    if provider not in VALID_PROVIDERS:
        provider = "gemini"
    return provider, api_key_from_env(provider)


def _read_ui_settings_file() -> dict:
    if not UI_SETTINGS_FILE.is_file():
        return {}
    try:
        data = json.loads(UI_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_ui_defaults() -> tuple[str, str]:
    """Provedor/chave para a UI: preferências salvas, senão .env."""
    data = _read_ui_settings_file()
    provider = str(data.get("llm_provider") or "").strip().lower()
    if provider not in VALID_PROVIDERS:
        return env_defaults()

    keys = data.get("api_keys")
    if isinstance(keys, dict):
        saved = str(keys.get(provider) or "").strip()
        if saved:
            return provider, saved

    # Formato legado / simples: um único api_key no JSON.
    single = str(data.get("api_key") or "").strip()
    if single:
        return provider, single

    # Provedor lembrado, chave ainda no .env.
    return provider, api_key_from_env(provider)


def persisted_api_key(provider: str) -> str:
    """Chave salva para o provedor; vazio se não houver memória local."""
    normalized = provider.strip().lower()
    if normalized not in VALID_PROVIDERS:
        return ""
    data = _read_ui_settings_file()
    keys = data.get("api_keys")
    if isinstance(keys, dict):
        saved = str(keys.get(normalized) or "").strip()
        if saved:
            return saved
    if str(data.get("llm_provider") or "").strip().lower() == normalized:
        return str(data.get("api_key") or "").strip()
    return ""


def save_ui_settings(provider: str, api_key: str) -> Path:
    """Persiste provedor ativo e chave (por provedor) em data/ui_settings.json."""
    normalized = provider.strip().lower()
    if normalized not in VALID_PROVIDERS:
        raise ConfigError(
            f"Provedor inválido: '{provider}'. Use um de: {', '.join(VALID_PROVIDERS)}."
        )
    key = api_key.strip()

    data = _read_ui_settings_file()
    keys = data.get("api_keys")
    if not isinstance(keys, dict):
        keys = {}
    # Preserva chaves dos outros provedores já salvas.
    for name in VALID_PROVIDERS:
        if name not in keys:
            keys[name] = ""
        else:
            keys[name] = str(keys[name] or "").strip()
    keys[normalized] = key

    payload = {
        "llm_provider": normalized,
        "api_keys": keys,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UI_SETTINGS_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return UI_SETTINGS_FILE


def load_settings() -> Settings:
    """Lê e valida preferências UI / .env (uso solo / scripts)."""
    provider, api_key = load_ui_defaults()
    try:
        return settings_from_values(provider, api_key)
    except ConfigError as exc:
        key_var = _key_var_for(provider)
        raise ConfigError(
            f"Chave de API ausente para provedor={provider} "
            f"(salve em Configurações ou defina {key_var} no .env)."
        ) from exc
