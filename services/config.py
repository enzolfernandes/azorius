"""Ponto único de configuração da aplicação.

Carrega o .env (fallback para uso solo) e expõe um objeto imutável de
configuração. A UI pode montar Settings a partir de valores da sessão sem
ler o ambiente diretamente — nenhum outro módulo deve chamar os.getenv.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RULES_FILE = DATA_DIR / "MagicCompRules.txt"


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


def load_settings() -> Settings:
    """Lê e valida o .env (uso solo / scripts). Preferir settings_from_values na UI."""
    provider, api_key = env_defaults()
    try:
        return settings_from_values(provider, api_key)
    except ConfigError as exc:
        key_var = _key_var_for(provider)
        raise ConfigError(
            f"{key_var} não definida no .env (obrigatória para LLM_PROVIDER={provider})."
        ) from exc
