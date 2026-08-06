"""Análise heurística de decklist colada (gaps por pacote).

Módulo puro: normaliza via Scryfall (identidade/oracle) e precifica em R$
via LigaMagic. Orçamento do mercado Brasil não usa USD.
Metas de pacote vêm de services.deck_composition.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from services.config import resolve_brl_price
from services.deck_composition import (
    PACKAGE_LABELS_PT,
    PACKAGE_RANGES,
    format_range,
    gaps_from_counts,
)
from services.decklist_parse import parse_pasted_decklist
from services.ligamagic_prices import fetch_ligamagic_brl
from services.scryfall_api import fetch_card_market_info, fetch_commander

# Compat: mínimo da faixa por pacote (sem protection).
PACKAGE_TARGETS: dict[str, int] = {
    pkg: lo for pkg, (lo, _hi) in PACKAGE_RANGES.items()
}


def _classify(card: dict[str, Any]) -> str:
    type_line = (card.get("type_line") or "").lower()
    oracle = (card.get("oracle_text") or "").lower()
    name = (card.get("name") or "").lower()

    if "land" in type_line:
        return "lands"
    if any(
        k in oracle
        for k in (
            "destroy all",
            "exile all",
            "each creature",
            "all creatures",
            "board wipe",
        )
    ) or "wrath" in name:
        return "board_wipe"
    if any(
        k in oracle
        for k in (
            "destroy target",
            "exile target",
            "counter target",
            "return target",
            "fight",
            "-destroy",
        )
    ) or "removal" in name:
        return "removal"
    if any(
        k in oracle
        for k in (
            "add {",
            "search your library for a land",
            "put a land",
            "mana of any color",
        )
    ) or "sol ring" in name:
        return "ramp"
    if any(
        k in oracle
        for k in (
            "draw a card",
            "draw two",
            "draw three",
            "look at the top",
            "scry",
        )
    ):
        return "draw"
    # Proteção e restante contam como sinergia/tema na auditoria de gaps.
    return "synergy"


def normalize_decklist(text: str) -> dict[str, Any]:
    """Parse + Scryfall (nome) + LigaMagic (R$): entradas, gaps e total BRL."""
    parsed = parse_pasted_decklist(text)
    resolved: list[dict[str, Any]] = []
    missing: list[str] = []
    unpriced: list[str] = []
    by_package: dict[str, list[dict[str, Any]]] = defaultdict(list)
    total_brl = 0.0

    for qty, name in parsed:
        info = fetch_card_market_info(name)
        if info is None:
            missing.append(name)
            continue
        liga = fetch_ligamagic_brl(info["name"])
        brl, _source = resolve_brl_price(ligamagic_brl=liga, usd=info.get("usd"))
        entry = {
            "qty": qty,
            "name": info["name"],
            "mana_cost": info.get("mana_cost", ""),
            "cmc": info.get("cmc", 0.0),
            "brl": brl,
            "type_line": info.get("type_line", ""),
            "oracle_text": info.get("oracle_text", ""),
        }
        if brl is None:
            unpriced.append(info["name"])
        else:
            total_brl += float(brl) * qty
        pkg = _classify(entry)
        entry["package"] = pkg
        resolved.append(entry)
        by_package[pkg].append(entry)

    counts = {pkg: sum(c["qty"] for c in cards) for pkg, cards in by_package.items()}
    gaps = gaps_from_counts(counts)

    total_cards = sum(e["qty"] for e in resolved)
    return {
        "entries": resolved,
        "missing": missing,
        "unpriced": unpriced,
        "by_package": {k: list(v) for k, v in by_package.items()},
        "counts": counts,
        "gaps": gaps,
        "total_cards": total_cards,
        "total_brl": round(total_brl, 2),
        "currency": "BRL",
        "price_source": "ligamagic",
    }


def build_upgrade_brief(
    pasted_text: str,
    *,
    commander_name: str | None = None,
    bracket: int | None = None,
    budget_note: str = "",
) -> tuple[str, dict[str, Any]]:
    """Monta contexto estruturado para o LLM no modo melhoria."""
    analysis = normalize_decklist(pasted_text)
    commander = None
    if commander_name:
        commander = fetch_commander(commander_name)

    lines = ["## AUDITORIA DE DECK (Python — fatos, não inventar)"]
    if commander:
        lines.append(
            f"Comandante: {commander['name']} | CI: {','.join(commander.get('color_identity') or []) or 'C'}"
        )
    elif commander_name:
        lines.append(f"Comandante informado (não resolvido): {commander_name}")
    if bracket:
        lines.append(f"Bracket alvo: {bracket}")
    if budget_note:
        lines.append(f"Orçamento (R$ / texto do jogador): {budget_note}")

    lines.append(f"Cartas resolvidas: {analysis['total_cards']} (meta Commander ~99+1)")
    lines.append(
        f"Valor estimado LigaMagic: R$ {analysis['total_brl']} "
        f"(só cartas com preço BRL; fonte={analysis['price_source']})"
    )
    if analysis["missing"]:
        lines.append("Não encontradas no Scryfall: " + ", ".join(analysis["missing"][:20]))
    if analysis["unpriced"]:
        lines.append(
            "Sem preço em R$ (não inventar): "
            + ", ".join(analysis["unpriced"][:20])
        )

    lines.append("### Contagem por pacote (template)")
    for pkg, (lo, hi) in PACKAGE_RANGES.items():
        have = analysis["counts"].get(pkg, 0)
        label = PACKAGE_LABELS_PT.get(pkg, pkg)
        lines.append(f"- {label} ({pkg}): {have} (alvo {format_range(lo, hi)})")
    for pkg, count in sorted(analysis["counts"].items()):
        if pkg in PACKAGE_RANGES:
            continue
        lines.append(f"- {pkg}: {count}")

    if analysis["gaps"]:
        lines.append("### Gaps (heurística vs mínimo da faixa)")
        for gap in analysis["gaps"]:
            label = PACKAGE_LABELS_PT.get(gap["package"], gap["package"])
            lines.append(
                f"- {label}: tem {gap['have']}, alvo {gap['target_label']} "
                f"(faltam ~{gap['deficit']} para o mínimo)"
            )
    else:
        lines.append("### Gaps: nenhum déficit óbvio nos pacotes medidos.")

    lines.append("### Lista atual (Nx Nome · R$ unitário LigaMagic)")
    for entry in analysis["entries"]:
        brl = entry.get("brl")
        price_bit = f" · R$ {brl:.2f}" if brl is not None else " · sem preço BRL"
        lines.append(f"{entry['qty']}x {entry['name']}{price_bit}")

    lines.append(
        "\n## INSTRUÇÕES\n"
        "1. Foque em upgrades por pacote com déficit; sugira cortes e entradas.\n"
        "2. Orçamento só em R$. Use tools de preço; sem preço → "
        "não invente e não mencione dólar.\n"
        "3. Saída de listas no formato Nx Nome com # Headers.\n"
        "4. Não invente cartas fora da identidade se o comandante estiver claro."
    )
    return "\n".join(lines), analysis
