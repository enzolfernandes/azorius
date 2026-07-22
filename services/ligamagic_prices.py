"""Preços em BRL via LigaMagic (HTTP best-effort + cache em disco).

Fonte preferencial de orçamento no mercado Brasil. Em falha devolve None;
o chamador pode estimar BRL a partir do USD Scryfall (cotação fixa) sem
exibir dólar ao usuário.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from services.config import DATA_DIR

CACHE_DIR = DATA_DIR / "cache" / "ligamagic"
CACHE_TTL_SECONDS = 60 * 60 * 12  # 12h
REQUEST_TIMEOUT = 12
USER_AGENT = (
    "Mozilla/5.0 (compatible; AzoriusDeckbuilder/1.0; +https://github.com/local)"
)

# Aviso compartilhado com a UI quando a fonte BRL falha.
_last_warning: str | None = None


def get_last_warning() -> str | None:
    return _last_warning


def clear_last_warning() -> None:
    global _last_warning
    _last_warning = None


def _set_warning(message: str) -> None:
    global _last_warning
    _last_warning = message


def _cache_path(card_name: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", card_name.strip().lower())[:80]
    return CACHE_DIR / f"{safe or 'unknown'}.json"


def _read_cache(card_name: str) -> float | None:
    path = _cache_path(card_name)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    ts = data.get("ts")
    price = data.get("brl")
    if ts is None or price is None:
        return None
    if time.time() - float(ts) > CACHE_TTL_SECONDS:
        return None
    try:
        return float(price)
    except (TypeError, ValueError):
        return None


def _write_cache(card_name: str, brl: float) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(card_name)
    path.write_text(
        json.dumps({"name": card_name, "brl": brl, "ts": time.time()}, ensure_ascii=False),
        encoding="utf-8",
    )


def _parse_brl_text(text: str) -> float | None:
    """Converte strings tipo 'R$ 12,50' / '12.50' em float."""
    if not text:
        return None
    cleaned = text.strip()
    cleaned = cleaned.replace("R$", "").replace("r$", "").strip()
    cleaned = cleaned.replace(".", "").replace(",", ".") if "," in cleaned else cleaned
    cleaned = re.sub(r"[^\d.]", "", cleaned)
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def _extract_price_from_html(html: str) -> float | None:
    soup = BeautifulSoup(html, "html.parser")

    # Padrões comuns em páginas de carta / busca do LigaMagic.
    for selector in (
        ".precoMenor",
        ".price",
        ".card-price",
        "#card-price",
        "span.preco",
        "td.preco",
    ):
        node = soup.select_one(selector)
        if node:
            parsed = _parse_brl_text(node.get_text(" ", strip=True))
            if parsed is not None:
                return parsed

    # Fallback: primeira ocorrência de R$ no texto visível.
    for match in re.finditer(r"R\$\s*[\d.,]+", soup.get_text(" ", strip=True)):
        parsed = _parse_brl_text(match.group(0))
        if parsed is not None:
            return parsed
    return None


def fetch_ligamagic_brl(card_name: str) -> float | None:
    """Busca preço mínimo BRL no LigaMagic; None se indisponível."""
    name = (card_name or "").strip()
    if not name:
        return None

    cached = _read_cache(name)
    if cached is not None:
        return cached

    # Página de carta direta; fallback para busca textual.
    urls = [
        f"https://www.ligamagic.com.br/?view=cards/card&card={quote_plus(name)}",
        f"https://www.ligamagic.com.br/?view=cards/search&card={quote_plus(name)}",
    ]
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9"}

    for url in urls:
        try:
            response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            _set_warning(f"LigaMagic indisponível ({exc.__class__.__name__}).")
            return None
        if response.status_code != 200 or not response.text:
            continue
        price = _extract_price_from_html(response.text)
        if price is not None:
            _write_cache(name, price)
            return price

    _set_warning("Preço LigaMagic não encontrado para uma ou mais cartas.")
    return None


def lookup_prices_brl(card_names: list[str]) -> list[dict[str, Any]]:
    """Resolve nomes com preço BRL (found=False se falhar)."""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in card_names or []:
        if not isinstance(raw, str):
            continue
        query = raw.strip()
        if not query or query.lower() in seen:
            continue
        seen.add(query.lower())
        brl = fetch_ligamagic_brl(query)
        results.append(
            {
                "query": query,
                "name": query,
                "brl": brl,
                "currency": "BRL",
                "source": "ligamagic" if brl is not None else None,
                "found": brl is not None,
            }
        )
    return results
