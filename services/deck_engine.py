"""Construção de listas Commander com lógica Python estrita.

Módulo puro: sem Streamlit e sem LLM. Filtra identidade de cor, equilibra
orçamento/curva e monta o payload que o apresentador (llm_engine) apenas
narra — a matemática do deck fica toda aqui.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from .scryfall_api import fetch_card_market_info

DECK_SIZE = 99

# Curvas-alvo por Bracket (slots por CMC 0..7+). Soma de cada mapa = 99.
# Brackets baixos: mais top-end / mana barata; altos: mais low-curve.
BRACKET_CURVES: dict[int, dict[int, int]] = {
    1: {0: 38, 1: 8, 2: 12, 3: 14, 4: 12, 5: 8, 6: 5, 7: 2},
    2: {0: 37, 1: 9, 2: 13, 3: 14, 4: 12, 5: 8, 6: 4, 7: 2},
    3: {0: 36, 1: 11, 2: 15, 3: 14, 4: 11, 5: 7, 6: 3, 7: 2},
    4: {0: 35, 1: 14, 2: 16, 3: 14, 4: 10, 5: 6, 6: 3, 7: 1},
    5: {0: 34, 1: 16, 2: 18, 3: 14, 4: 9, 5: 5, 6: 2, 7: 1},
}

# Básicos usados para completar até 99 quando o pool Scryfall não basta.
_BASIC_LANDS: dict[str, dict[str, Any]] = {
    "W": {
        "name": "Plains",
        "color_identity": ["W"],
        "cmc": 0.0,
        "prices": {"usd": 0.0},
        "type_line": "Basic Land — Plains",
        "oracle_text": "({T}: Add {W}.)",
    },
    "U": {
        "name": "Island",
        "color_identity": ["U"],
        "cmc": 0.0,
        "prices": {"usd": 0.0},
        "type_line": "Basic Land — Island",
        "oracle_text": "({T}: Add {U}.)",
    },
    "B": {
        "name": "Swamp",
        "color_identity": ["B"],
        "cmc": 0.0,
        "prices": {"usd": 0.0},
        "type_line": "Basic Land — Swamp",
        "oracle_text": "({T}: Add {B}.)",
    },
    "R": {
        "name": "Mountain",
        "color_identity": ["R"],
        "cmc": 0.0,
        "prices": {"usd": 0.0},
        "type_line": "Basic Land — Mountain",
        "oracle_text": "({T}: Add {R}.)",
    },
    "G": {
        "name": "Forest",
        "color_identity": ["G"],
        "cmc": 0.0,
        "prices": {"usd": 0.0},
        "type_line": "Basic Land — Forest",
        "oracle_text": "({T}: Add {G}.)",
    },
}
_COLORLESS_BASIC = {
    "name": "Wastes",
    "color_identity": [],
    "cmc": 0.0,
    "prices": {"usd": 0.0},
    "type_line": "Basic Land",
    "oracle_text": "({T}: Add {C}.)",
}


def _card_price(card: dict) -> float:
    """USD da carta; sem preço = infinito (fica por último / fora do corte barato)."""
    usd = (card.get("prices") or {}).get("usd")
    if usd is None:
        return float("inf")
    try:
        return float(usd)
    except (TypeError, ValueError):
        return float("inf")


def _cmc_bucket(card: dict) -> int:
    """Agrupa CMC em 0..7 (7 = 7+)."""
    try:
        cmc = float(card.get("cmc") or 0)
    except (TypeError, ValueError):
        cmc = 0.0
    return min(max(int(cmc), 0), 7)


def enforce_color_identity(
    commander_colors: list[str], card_pool: list[dict]
) -> list[dict]:
    """Mantém só cartas cuja color_identity ⊆ identidade do comandante."""
    allowed = {c.upper() for c in commander_colors}
    legal: list[dict] = []
    for card in card_pool:
        identity = {c.upper() for c in (card.get("color_identity") or [])}
        if identity <= allowed:
            legal.append(card)
    return legal


def _curve_achieved(deck: list[dict]) -> dict[int, int]:
    counts: Counter[int] = Counter(_cmc_bucket(card) for card in deck)
    return {bucket: counts.get(bucket, 0) for bucket in range(8)}


def _make_basic(color: str) -> dict:
    if color in _BASIC_LANDS:
        return dict(_BASIC_LANDS[color])
    return dict(_COLORLESS_BASIC)


def _pad_with_basics(
    deck: list[dict],
    commander_colors: list[str],
    max_budget: float,
    spent: float,
) -> list[dict]:
    """Completa até 99 com básicos (preço 0) na identidade do comandante."""
    result = list(deck)
    colors = [c.upper() for c in commander_colors if c.upper() in _BASIC_LANDS]
    if not colors:
        colors = [""]
    idx = 0
    while len(result) < DECK_SIZE and spent <= max_budget + 1e-9:
        result.append(_make_basic(colors[idx % len(colors)]))
        idx += 1
    return result


def enforce_budget_and_curve(
    card_pool: list[dict],
    max_budget: float,
    target_curve: dict[int, int],
    *,
    commander_colors: list[str] | None = None,
) -> dict:
    """Seleciona até 99 cartas respeitando orçamento USD e curva-alvo.

    Retorno:
        {
          "deck": list[dict],
          "total_price": float,
          "curve_achieved": dict[int, int],
          "shortfall": int,  # quantas cartas faltaram para 99 antes dos básicos
          "target_curve": dict[int, int],
        }
    """
    # Normaliza a curva-alvo para buckets 0..7.
    target = {b: int(target_curve.get(b, 0)) for b in range(8)}
    # Se a soma da curva não for 99, redistribui o residual no CMC 0 (lands).
    curve_sum = sum(target.values())
    if curve_sum < DECK_SIZE:
        target[0] += DECK_SIZE - curve_sum
    elif curve_sum > DECK_SIZE:
        # Corta do top-end para baixo até caber em 99.
        overflow = curve_sum - DECK_SIZE
        for bucket in range(7, -1, -1):
            cut = min(target[bucket], overflow)
            target[bucket] -= cut
            overflow -= cut
            if overflow <= 0:
                break

    by_bucket: dict[int, list[dict]] = {b: [] for b in range(8)}
    for card in card_pool:
        by_bucket[_cmc_bucket(card)].append(card)
    for bucket in by_bucket:
        by_bucket[bucket].sort(key=_card_price)

    selected: list[dict] = []
    selected_names: set[str] = set()
    spent = 0.0
    slots_filled = {b: 0 for b in range(8)}

    def try_add(card: dict) -> bool:
        nonlocal spent
        name = (card.get("name") or "").strip().lower()
        if not name or name in selected_names:
            return False
        price = _card_price(card)
        if price == float("inf"):
            return False
        if spent + price > max_budget + 1e-9:
            return False
        selected.append(card)
        selected_names.add(name)
        spent += price
        slots_filled[_cmc_bucket(card)] += 1
        return True

    # 1) Preenche slots da curva, preferindo as mais baratas de cada bucket.
    for bucket in range(8):
        need = target[bucket]
        for card in by_bucket[bucket]:
            if slots_filled[bucket] >= need:
                break
            try_add(card)

    # 2) Completa até 99 com as mais baratas restantes (qualquer CMC).
    leftovers = sorted(
        (
            card
            for bucket in range(8)
            for card in by_bucket[bucket]
            if (card.get("name") or "").strip().lower() not in selected_names
        ),
        key=_card_price,
    )
    for card in leftovers:
        if len(selected) >= DECK_SIZE:
            break
        try_add(card)

    shortfall_before_basics = max(0, DECK_SIZE - len(selected))
    colors = list(commander_colors or [])
    if len(selected) < DECK_SIZE:
        selected = _pad_with_basics(selected, colors, max_budget, spent)
        # Básicos têm preço 0; spent permanece.

    # Garante teto de 99 (não ultrapassar).
    selected = selected[:DECK_SIZE]
    total_price = sum(
        0.0 if _card_price(c) == float("inf") else _card_price(c) for c in selected
    )

    return {
        "deck": selected,
        "total_price": round(total_price, 2),
        "curve_achieved": _curve_achieved(selected),
        "shortfall": shortfall_before_basics,
        "target_curve": target,
    }


def generate_decklist_prompt(
    commander_name: str,
    optimized_pool: list[dict] | dict,
    user_request: str,
) -> str:
    """Monta o payload estruturado para o LLM apresentador (sem recalcular).

    `optimized_pool` aceita a lista de cartas ou o dict retornado por
    `enforce_budget_and_curve` (com metadados).
    """
    if isinstance(optimized_pool, dict) and "deck" in optimized_pool:
        deck = list(optimized_pool.get("deck") or [])
        total_price = optimized_pool.get("total_price")
        curve = optimized_pool.get("curve_achieved") or _curve_achieved(deck)
        shortfall = optimized_pool.get("shortfall", 0)
        target = optimized_pool.get("target_curve") or {}
    else:
        deck = list(optimized_pool or [])
        total_price = round(
            sum(
                0.0 if _card_price(c) == float("inf") else _card_price(c) for c in deck
            ),
            2,
        )
        curve = _curve_achieved(deck)
        shortfall = max(0, DECK_SIZE - len(deck))
        target = {}

    lines = []
    for card in deck:
        price = _card_price(card)
        price_s = "N/A" if price == float("inf") else f"${price:.2f}"
        lines.append(
            f"- {card.get('name', '?')} | CMC {card.get('cmc', 0)} | {price_s} | "
            f"{card.get('type_line', '')}"
        )
    deck_block = "\n".join(lines) if lines else "(lista vazia)"

    curve_lines = ", ".join(f"CMC{b}={curve.get(b, 0)}" for b in range(8))
    target_lines = (
        ", ".join(f"CMC{b}={target.get(b, 0)}" for b in range(8)) if target else "N/A"
    )
    request = (user_request or "").strip() or "(sem pedido adicional)"

    return f"""## DECKLIST OFICIAL (GERADA EM PYTHON — NÃO ALTERAR)
Comandante: {commander_name}
Cartas no deck (99-slot, sem o comandante): {len(deck)}
Orçamento total (USD, soma Scryfall): ${total_price}
Curva alcançada: {curve_lines}
Curva-alvo: {target_lines}
Shortfall antes de básicos: {shortfall}

### Pedido do jogador
{request}

### Lista (nome | CMC | preço | type_line)
{deck_block}

## INSTRUÇÕES AO APRESENTADOR
1. Apresente esta lista em Português do Brasil de forma didática.
2. NÃO adicione, remova, substitua nem reordene cartas.
3. NÃO recalcule orçamento, curva de mana nem identidade de cor — os números acima são finais.
4. Explique brevemente o racional geral (como a lista atende o pedido) sem inventar cartas.
5. Comece com "Planinauta, " e organize por categorias úteis (terrenos, ramp, draw, interação, etc.)
   usando APENAS nomes que aparecem na lista.
"""


# ---------------------------------------------------------------------------
# Tools do Deckbuilder agentic (function calling)
# ---------------------------------------------------------------------------

DECKBUILDER_TOOLS: list[dict[str, Any]] = [
    {
        "name": "lookup_card_prices",
        "description": (
            "Consulta preços USD e custo de mana de cartas no Scryfall. "
            "Use antes de sugerir um pacote quando o jogador definiu orçamento."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "card_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Nomes das cartas a precificar (inglês preferencial).",
                }
            },
            "required": ["card_names"],
        },
    },
    {
        "name": "summarize_package_budget",
        "description": (
            "Soma o preço USD de um pacote de cartas e compara com o orçamento "
            "máximo conversado no Passo 0. Use para validar se o pacote cabe no budget."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "card_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Nomes das cartas do pacote.",
                },
                "max_budget_usd": {
                    "type": "number",
                    "description": (
                        "Orçamento máximo em USD. Omita ou use null se o jogador "
                        "disse sem limite."
                    ),
                },
            },
            "required": ["card_names"],
        },
    },
]


def lookup_card_prices(card_names: list[str]) -> list[dict[str, Any]]:
    """Resolve cada nome no Scryfall e devolve name, mana_cost, usd, found."""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_name in card_names or []:
        if not isinstance(raw_name, str):
            continue
        query = raw_name.strip()
        if not query or query.lower() in seen:
            continue
        seen.add(query.lower())
        info = fetch_card_market_info(query)
        if info is None:
            results.append(
                {
                    "query": query,
                    "name": query,
                    "mana_cost": "",
                    "usd": None,
                    "found": False,
                }
            )
            continue
        results.append(
            {
                "query": query,
                "name": info["name"],
                "mana_cost": info.get("mana_cost", ""),
                "usd": info.get("usd"),
                "found": True,
            }
        )
    return results


def summarize_package_budget(
    card_names: list[str], max_budget_usd: float | None = None
) -> dict[str, Any]:
    """Soma USD do pacote e indica se respeita o budget conversado."""
    priced = lookup_card_prices(card_names)
    total = 0.0
    missing: list[str] = []
    priced_cards: list[dict[str, Any]] = []
    for item in priced:
        if not item["found"] or item["usd"] is None:
            missing.append(item["name"])
            continue
        total += float(item["usd"])
        priced_cards.append(item)

    budget: float | None
    if max_budget_usd is None:
        budget = None
        within = True
    else:
        try:
            budget = float(max_budget_usd)
        except (TypeError, ValueError):
            budget = None
            within = True
        else:
            within = total <= budget + 1e-9

    return {
        "total_usd": round(total, 2),
        "max_budget_usd": budget,
        "within_budget": within,
        "missing": missing,
        "cards": priced_cards,
        "card_count_priced": len(priced_cards),
    }


def run_deckbuilder_tool(name: str, args: dict[str, Any]) -> str:
    """Dispatcher de tools: executa e serializa o resultado em JSON."""
    try:
        if name == "lookup_card_prices":
            names = args.get("card_names") or []
            if not isinstance(names, list):
                names = [str(names)]
            payload = lookup_card_prices([str(n) for n in names])
            return json.dumps(payload, ensure_ascii=False)
        if name == "summarize_package_budget":
            names = args.get("card_names") or []
            if not isinstance(names, list):
                names = [str(names)]
            max_budget = args.get("max_budget_usd", None)
            payload = summarize_package_budget(
                [str(n) for n in names], max_budget_usd=max_budget
            )
            return json.dumps(payload, ensure_ascii=False)
        return json.dumps({"error": f"Tool desconhecida: {name}"}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
