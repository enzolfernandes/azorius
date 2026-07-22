"""Montagem acumulada da decklist a partir das mensagens do Deckbuilder.

Módulo puro: parseia `# Categoria` / `Nx Nome` e consolida por nome.
"""

from __future__ import annotations

from collections import OrderedDict

from services.decklist_parse import parse_card_line, parse_decklist_text


def extract_entries_from_text(text: str) -> list[tuple[str, int, str]]:
    """Extrai (seção, qty, nome) de uma mensagem."""
    parsed = parse_decklist_text(text or "")
    entries: list[tuple[str, int, str]] = []
    for block in parsed.blocks:
        for qty, name in block.cards:
            entries.append((block.title, qty, name))
    if entries:
        return entries

    # Fallback: linhas Nx Nome sem header.
    for line in (text or "").splitlines():
        card = parse_card_line(line)
        if card:
            qty, name = card
            entries.append(("Outros", qty, name))
    return entries


def merge_entries(
    current: list[dict],
    new_entries: list[tuple[str, int, str]],
) -> list[dict]:
    """Mescla entradas novas na lista acumulada.

    Mesmo nome (case-insensitive): mantém qty máxima e seção mais recente.
    """
    by_key: OrderedDict[str, dict] = OrderedDict()
    for item in current or []:
        name = (item.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        by_key[key] = {
            "name": name,
            "qty": int(item.get("qty") or 1),
            "section": (item.get("section") or "Outros").strip() or "Outros",
        }

    for section, qty, name in new_entries:
        name = (name or "").strip()
        if not name or qty < 1:
            continue
        key = name.lower()
        section = (section or "Outros").strip() or "Outros"
        if key in by_key:
            prev = by_key[key]
            by_key[key] = {
                "name": prev["name"],
                "qty": max(int(prev["qty"]), int(qty)),
                "section": section,
            }
        else:
            by_key[key] = {"name": name, "qty": int(qty), "section": section}

    return list(by_key.values())


def rebuild_from_messages(messages: list[dict]) -> list[dict]:
    """Reconstrói a lista a partir de todas as mensagens do assistente."""
    assembled: list[dict] = []
    for msg in messages or []:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content") or ""
        assembled = merge_entries(assembled, extract_entries_from_text(content))
    return assembled


def total_cards(assembled: list[dict]) -> int:
    return sum(int(item.get("qty") or 0) for item in assembled or [])


def group_by_section(assembled: list[dict]) -> list[tuple[str, list[dict]]]:
    """Agrupa preservando ordem de primeira aparição da seção."""
    order: list[str] = []
    groups: dict[str, list[dict]] = {}
    for item in assembled or []:
        section = (item.get("section") or "Outros").strip() or "Outros"
        if section not in groups:
            groups[section] = []
            order.append(section)
        groups[section].append(item)
    for section in order:
        groups[section].sort(key=lambda c: (c.get("name") or "").lower())
    return [(section, groups[section]) for section in order]


def format_export(assembled: list[dict]) -> str:
    """Texto importável `# Seção` + `Nx Nome`."""
    lines: list[str] = []
    for section, cards in group_by_section(assembled):
        lines.append(f"# {section}")
        for card in cards:
            lines.append(f"{int(card['qty'])}x {card['name']}")
        lines.append("")
    return "\n".join(lines).rstrip() + ("\n" if lines else "")
