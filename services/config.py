"""Ponto único de configuração da aplicação.

Carrega o .env e expõe um objeto imutável de configuração. Nenhum outro módulo
deve ler variáveis de ambiente diretamente — isso mantém a configuração
centralizada e facilita a futura migração para FastAPI (basta trocar a origem
das variáveis, os services não mudam).
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

VALID_PROVIDERS = ("gemini", "openai")


class ConfigError(Exception):
    """Configuração ausente ou inválida no .env."""


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    api_key: str


def load_settings() -> Settings:
    """Lê e valida o .env, retornando as configurações da aplicação.

    Levanta ConfigError com mensagem clara se o provedor for inválido ou a
    chave de API correspondente estiver ausente.
    """
    provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    if provider not in VALID_PROVIDERS:
        raise ConfigError(
            f"LLM_PROVIDER inválido: '{provider}'. Use um de: {', '.join(VALID_PROVIDERS)}."
        )

    key_var = "GOOGLE_API_KEY" if provider == "gemini" else "OPENAI_API_KEY"
    api_key = os.getenv(key_var, "").strip()
    if not api_key:
        raise ConfigError(f"{key_var} não definida no .env (obrigatória para LLM_PROVIDER={provider}).")

    return Settings(llm_provider=provider, api_key=api_key)
