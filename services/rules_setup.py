"""Download das Comprehensive Rules oficiais (Wizards).

Usado pelo CLI (`scripts/setup_rules.py`) e pelo bootstrap do Chroma quando o
arquivo ainda não existe (ex.: Streamlit Community Cloud, onde `data/` não
está no Git).
"""

from __future__ import annotations

import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from .config import RULES_FILE

RULES_PAGE_URL = "https://magic.wizards.com/en/rules"

# A Wizards bloqueia requisições sem User-Agent de navegador.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}

TXT_LINK_PATTERN = re.compile(r"MagicCompRules.*\.txt", re.IGNORECASE)


class RulesDownloadError(Exception):
    """Falha ao localizar ou baixar o arquivo oficial de regras."""


def find_rules_txt_url() -> str:
    """Raspa a página oficial e retorna a URL atual do .txt das Comprehensive Rules."""
    try:
        response = requests.get(RULES_PAGE_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RulesDownloadError(f"Não foi possível acessar {RULES_PAGE_URL}: {exc}") from exc

    soup = BeautifulSoup(response.text, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if TXT_LINK_PATTERN.search(href):
            if href.startswith("//"):
                return f"https:{href}"
            if href.startswith("/"):
                return f"https://magic.wizards.com{href}"
            return href

    raise RulesDownloadError(
        "Nenhum link .txt das Comprehensive Rules encontrado na página oficial."
    )


def download_rules(url: str, destination: Path) -> None:
    """Baixa o .txt das regras e salva em UTF-8, removendo BOM se presente."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=60)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RulesDownloadError(f"Falha no download de {url}: {exc}") from exc

    text = response.content.decode("utf-8-sig", errors="replace")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")


def ensure_rules_file(destination: Path = RULES_FILE) -> Path:
    """Garante que MagicCompRules.txt existe; baixa da Wizards se faltar."""
    if destination.exists() and destination.stat().st_size > 0:
        return destination
    url = find_rules_txt_url()
    download_rules(url, destination)
    if not destination.exists() or destination.stat().st_size == 0:
        raise RulesDownloadError(f"Download concluído, mas o arquivo está vazio: {destination}")
    return destination
