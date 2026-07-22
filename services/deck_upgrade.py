"""Análise heurística de decklist colada (gaps por pacote).

Módulo puro: normaliza via Scryfall e classifica por type_line/CMC/oracle.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from services.decklist_parse import parse_pasted_decklist
from services.scryfall_api import fetch_card_market_info, fetch_commander

PACKAGE_TARGETS: dict[str, int] = {
    "ramp": 10,
    "draw": 10,
    "removal": 8,
    "board_wipe": 2,
    "protection": 4,
    "synergy": 0,  # sem meta fixa
    "lands": 36,
    "other": 0,
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
            "hexproof",
            "indestructible",
            "protection from",
            "can't be countered",
            "ward",
        )
    ):
        return "protection"
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
    return "other"


def normalize_decklist(text: str) -> dict[str, Any]:
    """Parse + Scryfall: entradas resolvidas, não encontradas e por pacote."""
    parsed = parse_pasted_decklist(text)
    resolved: list[dict[str, Any]] = []
    missing: list[str] = []
    by_package: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for qty, name in parsed:
        info = fetch_card_market_info(name)
        if info is None:
            missing.append(name)
            continue
        entry = {
            "qty": qty,
            "name": info["name"],
            "mana_cost": info.get("mana_cost", ""),
            "cmc": info.get("cmc", 0.0),
            "usd": info.get("usd"),
            "type_line": info.get("type_line", ""),
            "oracle_text": info.get("oracle_text", ""),
        }
        # oracle pode não vir do market_info — busca mínima ok
        pkg = _classify(entry)
        entry["package"] = pkg
        resolved.append(entry)
        by_package[pkg].append(entry)

    counts = {pkg: sum(c["qty"] for c in cards) for pkg, cards in by_package.items()}
    gaps: list[dict[str, Any]] = []
    for pkg, target in PACKAGE_TARGETS.items():
        if target <= 0:
            continue
        have = counts.get(pkg, 0)
        if have < target:
            gaps.append(
                {
                    "package": pkg,
                    "have": have,
                    "target": target,
                    "deficit": target - have,
                }
            )

    total_cards = sum(e["qty"] for e in resolved)
    return {
        "entries": resolved,
        "missing": missing,
        "by_package": {k: list(v) for k, v in by_package.items()},
        "counts": counts,
        "gaps": gaps,
        "total_cards": total_cards,
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
        lines.append(f"Orçamento: {budget_note}")

    lines.append(f"Cartas resolvidas: {analysis['total_cards']} (meta Commander ~99+1)")
    if analysis["missing"]:
        lines.append("Não encontradas no Scryfall: " + ", ".join(analysis["missing"][:20]))

    lines.append("### Contagem por pacote")
    for pkg, count in sorted(analysis["counts"].items()):
        lines.append(f"- {pkg}: {count}")

    if analysis["gaps"]:
        lines.append("### Gaps (heurística)")
        for gap in analysis["gaps"]:
            lines.append(
                f"- {gap['package']}: tem {gap['have']}, alvo ~{gap['target']} "
                f"(faltam ~{gap['deficit']})"
            )
    else:
        lines.append("### Gaps: nenhum déficit óbvio nos pacotes medidos.")

    lines.append("### Lista atual (Nx Nome)")
    for entry in analysis["entries"]:
        lines.append(f"{entry['qty']}x {entry['name']}")

    lines.append(
        "\n## INSTRUÇÕES\n"
        "1. Foque em upgrades por pacote com déficit; sugira cortes e entradas.\n"
        "2. Use tools de preço se houver orçamento.\n"
        "3. Saída de listas no formato Nx Nome com # Headers.\n"
        "4. Não invente cartas fora da identidade se o comandante estiver claro."
    )
    return "\n".join(lines), analysis
