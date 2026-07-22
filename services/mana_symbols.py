"""Converte notação Scryfall `{C}`, `{W/U}`, `{T}` em ícones SVG oficiais.

Módulo puro: devolve HTML com <img> apontando ao CDN do Scryfall.
"""

from __future__ import annotations

import html
import re
from urllib.parse import quote

# Conteúdo dentro de chaves: C, W/U, 2, B/P, T, CHAOS, etc.
MANA_SYMBOL_RE = re.compile(r"\{([^{}]+)\}")

SCRYFALL_SYMBOL_CDN = "https://svgs.scryfall.io/card-symbols/{code}.svg"

MANA_SYMBOL_CSS = """
<style>
img.az-mana {
  height: 1.15em;
  width: 1.15em;
  vertical-align: -0.2em;
  margin: 0 0.08em;
  display: inline;
}
</style>
"""


def symbol_svg_url(inner: str) -> str:
    """URL do SVG Scryfall para o miolo de um símbolo (ex.: 'C', 'W/U')."""
    code = (inner or "").strip().replace("/", "")
    # CDN usa maiúsculas (C, WU, BP, CHAOS…).
    code = code.upper()
    return SCRYFALL_SYMBOL_CDN.format(code=quote(code, safe=""))


def mana_symbol_img_html(full_or_inner: str) -> str:
    """Uma tag <img> para `{C}` ou `C`."""
    token = (full_or_inner or "").strip()
    if token.startswith("{") and token.endswith("}"):
        inner = token[1:-1]
        display = token
    else:
        inner = token
        display = f"{{{token}}}"
    if not inner:
        return html.escape(display)
    url = html.escape(symbol_svg_url(inner), quote=True)
    alt = html.escape(display)
    return (
        f'<img class="az-mana" src="{url}" alt="{alt}" title="{alt}" loading="lazy"/>'
    )


def replace_mana_symbols(text: str) -> str:
    """Troca `{…}` por ícones; o restante do texto permanece intacto (markdown ok)."""
    if not text or "{" not in text:
        return text or ""

    def _repl(match: re.Match[str]) -> str:
        return mana_symbol_img_html(match.group(0))

    return MANA_SYMBOL_RE.sub(_repl, text)


def replace_mana_symbols_escaped(text: str) -> str:
    """Como replace_mana_symbols, mas escapa trechos que não são símbolos."""
    if not text:
        return ""
    if "{" not in text:
        return html.escape(text)

    parts: list[str] = []
    last = 0
    for match in MANA_SYMBOL_RE.finditer(text):
        parts.append(html.escape(text[last : match.start()]))
        parts.append(mana_symbol_img_html(match.group(0)))
        last = match.end()
    parts.append(html.escape(text[last:]))
    return "".join(parts)


def has_mana_symbols(text: str) -> bool:
    return bool(text and MANA_SYMBOL_RE.search(text))
