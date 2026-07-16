"""Baixa o arquivo oficial de Regras Abrangentes (Comprehensive Rules) de MTG.

A Wizards publica o .txt com a data embutida no nome (ex.: "MagicCompRules 20250725.txt"),
então este script raspa a página oficial de regras, localiza dinamicamente o link do .txt
via regex e salva sempre com o nome fixo "MagicCompRules.txt" em /data.

Uso:
    python scripts/setup_rules.py
"""

import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

RULES_PAGE_URL = "https://magic.wizards.com/en/rules"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "MagicCompRules.txt"

# A Wizards bloqueia requisições sem User-Agent de navegador.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}

# Casa qualquer variação do nome do arquivo, independentemente da data embutida.
TXT_LINK_PATTERN = re.compile(r"MagicCompRules.*\.txt", re.IGNORECASE)


def find_rules_txt_url() -> str:
    """Raspa a página oficial e retorna a URL atual do .txt das Regras Abrangentes."""
    response = requests.get(RULES_PAGE_URL, headers=HEADERS, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if TXT_LINK_PATTERN.search(href):
            # Links podem vir relativos ao domínio.
            if href.startswith("//"):
                return f"https:{href}"
            if href.startswith("/"):
                return f"https://magic.wizards.com{href}"
            return href

    raise RuntimeError("Nenhum link .txt das Comprehensive Rules encontrado na página.")


def download_rules(url: str, destination: Path) -> None:
    """Baixa o .txt das regras e salva em UTF-8, removendo BOM se presente."""
    response = requests.get(url, headers=HEADERS, timeout=60)
    response.raise_for_status()

    # O arquivo oficial costuma vir em UTF-8 com BOM; normalizamos para UTF-8 puro.
    text = response.content.decode("utf-8-sig", errors="replace")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")


def main() -> int:
    try:
        print(f"Buscando link das Comprehensive Rules em {RULES_PAGE_URL} ...")
        url = find_rules_txt_url()
        print(f"Link encontrado: {url}")

        print("Baixando arquivo de regras...")
        download_rules(url, OUTPUT_PATH)

        size_kb = OUTPUT_PATH.stat().st_size / 1024
        print(f"OK: regras salvas em {OUTPUT_PATH} ({size_kb:.0f} KB)")
        return 0
    except (requests.RequestException, RuntimeError) as exc:
        print(f"ERRO: não foi possível baixar as regras automaticamente: {exc}")
        print(
            "Baixe manualmente o .txt em https://magic.wizards.com/en/rules "
            f"e salve como {OUTPUT_PATH}"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
