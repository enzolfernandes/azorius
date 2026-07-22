"""Marcação de nomes de cartas e HTML de preview flutuante (hover).

Módulo puro de apresentação auxiliar — sem Streamlit.
"""

from __future__ import annotations

import html
import re
from typing import Iterable

from services.mana_symbols import MANA_SYMBOL_CSS, mana_symbol_img_html, MANA_SYMBOL_RE

# Preview fixo no iframe (components.html) — segue o cursor.
PREVIEW_HOVER_SCRIPT = """
<script>
(function () {
  if (window.__azCardPreviewInit) return;
  window.__azCardPreviewInit = true;
  var tip = document.createElement("div");
  tip.id = "az-card-float";
  tip.style.cssText = [
    "display:none",
    "position:fixed",
    "z-index:2147483647",
    "pointer-events:none",
    "left:0",
    "top:0"
  ].join(";");
  document.body.appendChild(tip);

  function place(el) {
    var url = el.getAttribute("data-az-img");
    if (!url) return;
    tip.innerHTML = '<img src="' + url + '" alt="" style="' +
      "width:220px;height:auto;border-radius:12px;" +
      "box-shadow:0 8px 28px rgba(0,0,0,0.55);" +
      '"/>';
    tip.style.display = "block";
    var r = el.getBoundingClientRect();
    var w = tip.offsetWidth || 220;
    var h = tip.offsetHeight || 310;
    // Prefere ABAIXO do nome — evita cobrir a pergunta/linha atual.
    var x = r.left;
    var y = r.bottom + 10;
    if (x + w > window.innerWidth - 8) x = window.innerWidth - w - 8;
    if (x < 4) x = 4;
    if (y + h > window.innerHeight - 8) {
      y = r.top - h - 10;
    }
    if (y < 4) y = 4;
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }
  function hide() { tip.style.display = "none"; tip.innerHTML = ""; }

  document.addEventListener("mouseover", function (e) {
    var t = e.target.closest("[data-az-img]");
    if (!t) return;
    place(t);
  });
  document.addEventListener("mouseout", function (e) {
    var t = e.target.closest("[data-az-img]");
    if (!t) return;
    var rel = e.relatedTarget;
    if (rel && t.contains(rel)) return;
    hide();
  });
})();
</script>
"""

def _preview_css(*, dark: bool) -> str:
    """CSS com contraste explícito (iframe não herda o tema do Streamlit)."""
    if dark:
        fg, fg_muted, heading, mark, bg, border = (
            "#f3f4f6",
            "#d1d5db",
            "#ffffff",
            "#fbbf24",
            "#262730",
            "rgba(255,255,255,0.10)",
        )
    else:
        fg, fg_muted, heading, mark, bg, border = (
            "#111827",
            "#374151",
            "#0f172a",
            "#b45309",
            "#ffffff",
            "rgba(15,23,42,0.10)",
        )
    return f"""
<style>
html, body {{
  margin: 0;
  padding: 0;
  background: transparent !important;
  overflow-x: hidden;
}}
#az-card-float {{
  max-width: min(220px, 92vw);
}}
#az-card-float img {{
  max-width: 100%;
  height: auto;
  display: block;
}}
.az-msg {{
  margin: 0;
  padding: 0.85rem 1.05rem;
  font-family: "Source Sans Pro", "Segoe UI", system-ui, sans-serif;
  font-size: 1.06rem;
  line-height: 1.75;
  letter-spacing: 0.01em;
  color: {fg};
  background: {bg};
  border: 1px solid {border};
  border-radius: 0.55rem;
  -webkit-font-smoothing: antialiased;
}}
.az-msg p {{
  margin: 0 0 0.9em;
  color: {fg};
}}
.az-msg p:last-child {{ margin-bottom: 0; }}
.az-msg strong {{ font-weight: 700; color: {heading}; }}
.az-msg em {{ font-style: italic; color: {fg_muted}; }}
.az-msg ul, .az-msg ol {{
  margin: 0.4em 0 0.9em;
  padding-left: 1.4em;
  color: {fg};
}}
.az-msg li {{ margin: 0.3em 0; }}
.az-msg h1, .az-msg h2, .az-msg h3, .az-msg h4 {{
  margin: 1.05em 0 0.5em;
  line-height: 1.35;
  font-weight: 700;
  color: {heading};
}}
.az-msg h1 {{ font-size: 1.35rem; }}
.az-msg h2 {{ font-size: 1.22rem; }}
.az-msg h3 {{ font-size: 1.1rem; }}
.az-card-mark {{
  color: {mark};
  text-decoration: underline;
  text-decoration-thickness: 1.5px;
  text-underline-offset: 3px;
  cursor: pointer;
  font-weight: 700;
  white-space: normal;
}}
.az-card-line {{
  display: block;
  margin: 0.35rem 0;
  font-size: 1.05rem;
  line-height: 1.65;
  color: {fg};
}}
.az-card-qty {{
  opacity: 0.9;
  margin-right: 0.4rem;
  font-family: ui-monospace, "Cascadia Mono", monospace;
  font-size: 0.95em;
  color: {fg_muted};
}}
img.az-mana {{
  height: 1.2em;
  width: 1.2em;
  vertical-align: -0.22em;
  margin: 0 0.1em;
}}
</style>
"""


def card_mark_html(name: str, image_url: str | None, *, qty: int | None = None) -> str:
    """Span clicável/hoverável com data-az-img para o script de preview."""
    safe_name = html.escape(name)
    qty_html = (
        f'<span class="az-card-qty">{html.escape(str(qty))}x</span>'
        if qty is not None
        else ""
    )
    if image_url:
        safe_url = html.escape(image_url, quote=True)
        return (
            f'{qty_html}<span class="az-card-mark" data-az-img="{safe_url}">'
            f"{safe_name}</span>"
        )
    return f'{qty_html}<span class="az-card-mark">{safe_name}</span>'


def _name_lookup(cards: Iterable[dict]) -> dict[str, dict]:
    """Mapa lower(name) -> card (com image_url)."""
    out: dict[str, dict] = {}
    for card in cards:
        name = (card.get("name") or "").strip()
        if name:
            out[name.lower()] = card
    return out


def _format_mana_and_escape(text: str) -> str:
    """Ícones de mana + escape HTML (sem markdown)."""
    if not text:
        return ""
    slots: list[str] = []

    def _park(html_snippet: str) -> str:
        slots.append(html_snippet)
        return f"\x00{len(slots) - 1}\x00"

    working = MANA_SYMBOL_RE.sub(
        lambda m: _park(mana_symbol_img_html(m.group(0))), text
    )
    working = html.escape(working)
    for i, snippet in enumerate(slots):
        working = working.replace(f"\x00{i}\x00", snippet)
    return working


def _linkify_plain(text: str, cards: list[dict]) -> str:
    """Marca cartas conhecidas + mana num trecho sem **negrito**."""
    lookup = _name_lookup(cards)
    names = sorted(lookup.keys(), key=len, reverse=True)
    if not names:
        return _format_mana_and_escape(text)

    pattern = re.compile(
        r"(?<!\w)(" + "|".join(re.escape(n) for n in names) + r")(?!\w)",
        re.IGNORECASE,
    )
    parts: list[str] = []
    last = 0
    for match in pattern.finditer(text):
        parts.append(_format_mana_and_escape(text[last : match.start()]))
        original = match.group(1)
        card = lookup.get(original.lower())
        image = (card or {}).get("image_url")
        display = (card or {}).get("name") or original
        parts.append(card_mark_html(display, image))
        last = match.end()
    parts.append(_format_mana_and_escape(text[last:]))
    return "".join(parts)


def _linkify_inline(text: str, cards: list[dict]) -> str:
    """Negrito markdown primeiro; depois cartas + mana."""
    if not text:
        return ""
    chunks = re.split(r"(\*\*.+?\*\*)", text)
    out: list[str] = []
    for chunk in chunks:
        if chunk.startswith("**") and chunk.endswith("**") and len(chunk) >= 4:
            out.append(f"<strong>{_linkify_plain(chunk[2:-2], cards)}</strong>")
        else:
            out.append(_linkify_plain(chunk, cards))
    return "".join(out)


def linkify_known_cards(text: str, cards: list[dict]) -> str:
    """Converte prosa em HTML legível com cartas marcadas e mana em ícones."""
    if not text:
        return ""

    lines = text.replace("\r\n", "\n").split("\n")
    blocks: list[str] = []
    # ul: list[str]; ol: list[tuple[int, str]] (número original do markdown)
    list_buf: list = []
    list_ordered: bool | None = None

    def flush_list() -> None:
        nonlocal list_buf, list_ordered
        if not list_buf:
            return
        if list_ordered:
            items = "".join(
                f'<li value="{num}">{content}</li>' for num, content in list_buf
            )
            blocks.append(f"<ol>{items}</ol>")
        else:
            items = "".join(f"<li>{content}</li>" for content in list_buf)
            blocks.append(f"<ul>{items}</ul>")
        list_buf = []
        list_ordered = None

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            flush_list()
            continue

        header = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if header:
            flush_list()
            level = len(header.group(1))
            blocks.append(
                f"<h{level}>{_linkify_inline(header.group(2), cards)}</h{level}>"
            )
            continue

        ul = re.match(r"^[-*]\s+(.+)$", stripped)
        if ul:
            if list_ordered is True:
                flush_list()
            list_ordered = False
            list_buf.append(_linkify_inline(ul.group(1), cards))
            continue

        ol = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if ol:
            if list_ordered is False:
                flush_list()
            list_ordered = True
            list_buf.append((int(ol.group(1)), _linkify_inline(ol.group(2), cards)))
            continue

        flush_list()
        blocks.append(f"<p>{_linkify_inline(stripped, cards)}</p>")

    flush_list()
    return f'<div class="az-msg">{"".join(blocks)}</div>'


def wrap_preview_document(
    body_html: str, *, dark: bool = True, enable_hover: bool = True
) -> str:
    """Documento completo para components.html (CSS + body + JS opcional)."""
    # Garante wrapper .az-msg se o caller passou só linhas de decklist.
    if 'class="az-msg"' not in body_html:
        body_html = f'<div class="az-msg">{body_html}</div>'
    script = PREVIEW_HOVER_SCRIPT if enable_hover else ""
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'/>"
        f"{_preview_css(dark=dark)}{MANA_SYMBOL_CSS}</head><body>"
        f"{body_html}{script}</body></html>"
    )


def strip_hover_attrs(body_html: str) -> str:
    """Remove data-az-img para não acionar preview (ex.: mensagem do usuário)."""
    return re.sub(r'\s*data-az-img="[^"]*"', "", body_html)


def estimate_html_height(text: str, *, min_h: int = 100, max_h: int = 1100) -> int:
    lines = max(1, text.count("\n") + 1)
    # Mais folga: line-height maior + parágrafos
    return min(max_h, max(min_h, int(lines * 32 + 72)))
