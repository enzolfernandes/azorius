"""Construção de listas Commander com lógica Python estrita.

Módulo puro: sem Streamlit e sem LLM. Filtra identidade de cor, equilibra
orçamento/curva e monta o payload que o apresentador (llm_engine) apenas
narra — a matemática do deck fica toda aqui.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from .scryfall_api import (
    fetch_card_market_info,
    fetch_commander,
    fetch_commander_card_pool,
)

DECK_SIZE = 99
# Quotas de terreno (pad de básicos só preenche estes slots — nunca come magias).
LAND_SLOTS_MIN = 34
LAND_SLOTS_MAX = 38
# Se faltar mais que isto de magias precificadas, a geração falha em vez de virar 80 basics.
MAX_SPELL_SHORTFALL = 20
# Alvos mínimos de role entre as magias (heurística barata).
SPELL_ROLE_TARGETS: dict[str, int] = {
    "ramp": 8,
    "draw": 8,
    "interaction": 8,
}

# Curvas-alvo por Bracket (slots por CMC 0..7+). Soma de cada mapa = 99.
# Brackets baixos: mais top-end / mana barata; altos: mais low-curve.
BRACKET_CURVES: dict[int, dict[int, int]] = {
    1: {0: 38, 1: 8, 2: 12, 3: 14, 4: 12, 5: 8, 6: 5, 7: 2},
    2: {0: 37, 1: 9, 2: 13, 3: 14, 4: 12, 5: 8, 6: 4, 7: 2},
    3: {0: 36, 1: 11, 2: 15, 3: 14, 4: 11, 5: 7, 6: 3, 7: 2},
    4: {0: 35, 1: 14, 2: 16, 3: 14, 4: 10, 5: 6, 6: 3, 7: 1},
    5: {0: 34, 1: 16, 2: 18, 3: 14, 4: 9, 5: 5, 6: 2, 7: 1},
}

_BASIC_TYPE_TO_COLOR = {
    "plains": "W",
    "island": "U",
    "swamp": "B",
    "mountain": "R",
    "forest": "G",
}

_BASIC_LAND_NAMES = frozenset(
    {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}
)


# Básicos usados para completar até 99 quando o pool Scryfall não basta.
_BASIC_LANDS: dict[str, dict[str, Any]] = {
    "W": {
        "name": "Plains",
        "color_identity": ["W"],
        "cmc": 0.0,
        "prices": {"usd": 0.0, "brl": 0.0},
        "type_line": "Basic Land — Plains",
        "oracle_text": "({T}: Add {W}.)",
    },
    "U": {
        "name": "Island",
        "color_identity": ["U"],
        "cmc": 0.0,
        "prices": {"usd": 0.0, "brl": 0.0},
        "type_line": "Basic Land — Island",
        "oracle_text": "({T}: Add {U}.)",
    },
    "B": {
        "name": "Swamp",
        "color_identity": ["B"],
        "cmc": 0.0,
        "prices": {"usd": 0.0, "brl": 0.0},
        "type_line": "Basic Land — Swamp",
        "oracle_text": "({T}: Add {B}.)",
    },
    "R": {
        "name": "Mountain",
        "color_identity": ["R"],
        "cmc": 0.0,
        "prices": {"usd": 0.0, "brl": 0.0},
        "type_line": "Basic Land — Mountain",
        "oracle_text": "({T}: Add {R}.)",
    },
    "G": {
        "name": "Forest",
        "color_identity": ["G"],
        "cmc": 0.0,
        "prices": {"usd": 0.0, "brl": 0.0},
        "type_line": "Basic Land — Forest",
        "oracle_text": "({T}: Add {G}.)",
    },
}
_COLORLESS_BASIC = {
    "name": "Wastes",
    "color_identity": [],
    "cmc": 0.0,
    "prices": {"usd": 0.0, "brl": 0.0},
    "type_line": "Basic Land",
    "oracle_text": "({T}: Add {C}.)",
}


def _card_price(card: dict) -> float:
    """BRL (LigaMagic ou estimado); sem preço = infinito (fora do corte)."""
    brl = (card.get("prices") or {}).get("brl")
    if brl is None:
        return float("inf")
    try:
        return float(brl)
    except (TypeError, ValueError):
        return float("inf")


def enrich_cards_with_ligamagic_brl(card_pool: list[dict]) -> list[dict]:
    """Anexa `prices.brl`: LigaMagic primeiro; senão USD Scryfall × taxa fixa.

    O BRL estimado fica só no motor (orçamento). Não expõe USD na UI.
    """
    from .config import resolve_brl_price
    from .ligamagic_prices import fetch_ligamagic_brl

    enriched: list[dict] = []
    for card in card_pool:
        item = dict(card)
        prices = dict(item.get("prices") or {})
        name = (item.get("name") or "").strip()
        liga = None
        if prices.get("brl") is not None and prices.get("brl_source") == "ligamagic":
            liga = prices.get("brl")
        elif name:
            liga = fetch_ligamagic_brl(name)
        brl, source = resolve_brl_price(ligamagic_brl=liga, usd=prices.get("usd"))
        if brl is not None:
            prices["brl"] = brl
            prices["brl_source"] = source
        item["prices"] = prices
        enriched.append(item)
    return enriched


def _cmc_bucket(card: dict) -> int:
    """Agrupa CMC em 0..7 (7 = 7+)."""
    try:
        cmc = float(card.get("cmc") or 0)
    except (TypeError, ValueError):
        cmc = 0.0
    return min(max(int(cmc), 0), 7)


def _is_land(card: dict) -> bool:
    return "land" in (card.get("type_line") or "").lower()


def _is_basic_land(card: dict) -> bool:
    name = (card.get("name") or "").strip()
    if name in _BASIC_LAND_NAMES:
        return True
    return "basic land" in (card.get("type_line") or "").lower()


def _fetch_basic_colors(oracle: str) -> set[str]:
    """Cores dos básicos nomeados num fetch (oracle com 'search')."""
    text = (oracle or "").lower()
    if "search" not in text:
        return set()
    found: set[str] = set()
    for basic, color in _BASIC_TYPE_TO_COLOR.items():
        if basic in text:
            found.add(color)
    return found


def land_fits_commander_colors(card: dict, commander_colors: list[str]) -> bool:
    """Rejeita fetches que só (ou também) buscam básicos fora da identidade."""
    if not _is_land(card):
        return True
    searched = _fetch_basic_colors(card.get("oracle_text") or "")
    if not searched:
        return True
    allowed = {c.upper() for c in commander_colors}
    return searched <= allowed


def classify_spell_role(card: dict) -> str:
    """Heurística mínima: ramp / draw / interaction / other (não-land)."""
    if _is_land(card):
        return "land"
    type_line = (card.get("type_line") or "").lower()
    oracle = (card.get("oracle_text") or "").lower()
    name = (card.get("name") or "").lower()

    if any(
        k in oracle
        for k in (
            "destroy all",
            "exile all",
            "destroy target",
            "exile target",
            "counter target",
            "return target",
            "fight",
        )
    ) or any(k in name for k in ("wrath", "removal")):
        return "interaction"
    if any(
        k in oracle
        for k in (
            "hexproof",
            "indestructible",
            "protection from",
            "ward",
            "can't be countered",
        )
    ):
        return "interaction"
    if (
        any(
            k in oracle
            for k in (
                "add {",
                "search your library for a land",
                "put a land",
                "mana of any color",
            )
        )
        or "sol ring" in name
        or ("artifact" in type_line and "add {" in oracle)
    ):
        return "ramp"
    if any(
        k in oracle
        for k in (
            "draw a card",
            "draw two",
            "draw three",
            "draw four",
            "look at the top",
            "scry",
        )
    ):
        return "draw"
    return "other"


def filter_pool_for_commander(
    card_pool: list[dict], commander_colors: list[str]
) -> list[dict]:
    """Identidade de cor + fetches compatíveis com as cores do comandante."""
    legal = enforce_color_identity(commander_colors, card_pool)
    return [c for c in legal if land_fits_commander_colors(c, commander_colors)]


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


def _land_slot_target(target_curve: dict[int, int]) -> int:
    raw = int(target_curve.get(0, 36))
    return min(LAND_SLOTS_MAX, max(LAND_SLOTS_MIN, raw))


def _normalize_curve(target_curve: dict[int, int], size: int) -> dict[int, int]:
    target = {b: int(target_curve.get(b, 0)) for b in range(8)}
    curve_sum = sum(target.values())
    if curve_sum < size:
        target[0] += size - curve_sum
    elif curve_sum > size:
        overflow = curve_sum - size
        for bucket in range(7, -1, -1):
            cut = min(target[bucket], overflow)
            target[bucket] -= cut
            overflow -= cut
            if overflow <= 0:
                break
    return target


def _spell_curve_from_bracket(
    bracket_curve: dict[int, int], spell_slots: int
) -> dict[int, int]:
    """Curva de magias: tira a fatia de CMC0 reservada a terrenos."""
    land_slots = _land_slot_target(bracket_curve)
    adjusted = dict(bracket_curve)
    adjusted[0] = max(0, int(adjusted.get(0, 0)) - land_slots)
    return _normalize_curve(adjusted, spell_slots)


def _pad_lands_with_basics(
    lands: list[dict],
    commander_colors: list[str],
    land_target: int,
) -> list[dict]:
    """Completa só até `land_target` com básicos na identidade."""
    result = list(lands)
    colors = [c.upper() for c in commander_colors if c.upper() in _BASIC_LANDS]
    if not colors:
        colors = [""]
    idx = 0
    while len(result) < land_target:
        result.append(_make_basic(colors[idx % len(colors)]))
        idx += 1
    return result[:land_target]


def enforce_budget_and_curve(
    card_pool: list[dict],
    max_budget: float,
    target_curve: dict[int, int],
    *,
    commander_colors: list[str] | None = None,
) -> dict:
    """Seleciona até 99 cartas: magias primeiro (orçamento), lands depois.

    Terrenos caros não podem comer o budget antes das magias. Lands não-básicos
    usam só o residual; o restante dos slots de terreno vai de básico (R$ 0).
    """
    colors = list(commander_colors or [])
    land_target = _land_slot_target(target_curve)
    spell_slots = DECK_SIZE - land_target
    spell_curve = _spell_curve_from_bracket(target_curve, spell_slots)

    lands_pool = [c for c in card_pool if _is_land(c)]
    spells_pool = [c for c in card_pool if not _is_land(c)]

    selected_lands: list[dict] = []
    selected_spells: list[dict] = []
    selected_names: set[str] = set()
    spent = 0.0

    def try_add(card: dict, bucket: list[dict], cap: int) -> bool:
        nonlocal spent
        if len(bucket) >= cap:
            return False
        name = (card.get("name") or "").strip().lower()
        if not name or name in selected_names:
            return False
        price = _card_price(card)
        if price == float("inf"):
            return False
        if spent + price > max_budget + 1e-9:
            return False
        bucket.append(card)
        selected_names.add(name)
        spent += price
        return True

    # --- Magias primeiro (prioridade do orçamento) ---
    by_role: dict[str, list[dict]] = {
        "ramp": [],
        "draw": [],
        "interaction": [],
        "other": [],
    }
    for card in spells_pool:
        role = classify_spell_role(card)
        if role == "land":
            continue
        by_role.setdefault(role, []).append(card)
    for role in by_role:
        by_role[role].sort(key=_card_price)

    # 1) Mínimos de role (baratas dentro de cada pacote).
    role_filled = {role: 0 for role in SPELL_ROLE_TARGETS}
    for role, need in SPELL_ROLE_TARGETS.items():
        for card in by_role.get(role, []):
            if role_filled[role] >= need:
                break
            if try_add(card, selected_spells, spell_slots):
                role_filled[role] += 1

    # 2) Resto: sempre as mais baratas globais (curva vira métrica, não travamento).
    # Em orçamento baixo, forçar CMC-alvo impede encher os 63 slots.
    for card in sorted(spells_pool, key=_card_price):
        if len(selected_spells) >= spell_slots:
            break
        try_add(card, selected_spells, spell_slots)

    spell_shortfall = max(0, spell_slots - len(selected_spells))
    spent_after_spells = spent

    # --- Terrenos: só com residual do orçamento; resto = básicos grátis ---
    for card in sorted(lands_pool, key=_card_price):
        if len(selected_lands) >= land_target:
            break
        try_add(card, selected_lands, land_target)

    land_shortfall = max(0, land_target - len(selected_lands))
    selected_lands = _pad_lands_with_basics(selected_lands, colors, land_target)

    selected = list(selected_spells) + list(selected_lands)
    spell_slot_basics = 0
    if len(selected) < DECK_SIZE:
        before = len(selected)
        selected = _pad_lands_with_basics(selected, colors, DECK_SIZE)
        spell_slot_basics = len(selected) - before

    selected = selected[:DECK_SIZE]
    total_price = sum(
        0.0 if _card_price(c) == float("inf") else _card_price(c) for c in selected
    )
    role_counts = Counter(classify_spell_role(c) for c in selected_spells)

    return {
        "deck": selected,
        "total_price": round(total_price, 2),
        "currency": "BRL",
        "curve_achieved": _curve_achieved(selected),
        "target_curve": target_curve,
        "spell_curve": spell_curve,
        "shortfall": spell_shortfall + land_shortfall,
        "spell_shortfall": spell_shortfall,
        "land_shortfall": land_shortfall,
        "spell_count": len(selected_spells),
        "land_count": land_target,
        "land_target": land_target,
        "spell_slots": spell_slots,
        "role_counts": dict(role_counts),
        "basics_padded": land_shortfall + spell_slot_basics,
        "spell_slot_basics": spell_slot_basics,
        "degraded": spell_shortfall > 0,
        "spent_after_spells": round(spent_after_spells, 2),
        "budget_spent": round(spent, 2),
    }


def consolidate_card_counts(cards: list[dict]) -> list[tuple[str, int]]:
    """Agrupa cartas por nome com Counter; retorna [(nome, quantidade), ...]."""
    counts: Counter[str] = Counter(
        name
        for card in cards
        if (name := (card.get("name") or "").strip())
    )
    return sorted(counts.items(), key=lambda item: item[0].lower())


def format_decklist_export(
    cards: list[dict],
    *,
    commander_name: str | None = None,
) -> str:
    """Lista crua estilo Moxfield/Arena: '# Categoria' e linhas 'Nx Nome'.

    `# Terrenos` = qualquer land (type_line); `# Spells` = restante.
    """
    lines: list[str] = []
    if commander_name and commander_name.strip():
        lines.append("# Comandante")
        lines.append(f"1x {commander_name.strip()}")
        lines.append("")

    lands = [c for c in cards if _is_land(c)]
    spells = [c for c in cards if not _is_land(c)]

    if spells:
        lines.append("# Spells")
        for name, qty in consolidate_card_counts(spells):
            lines.append(f"{qty}x {name}")
        lines.append("")

    if lands:
        lines.append("# Terrenos")
        for name, qty in consolidate_card_counts(lands):
            lines.append(f"{qty}x {name}")

    return ("\n".join(lines).rstrip() + "\n") if lines else ""


def generate_decklist_prompt(
    commander_name: str,
    optimized_pool: list[dict] | dict,
    user_request: str,
) -> str:
    """Monta o payload estruturado para o LLM apresentador (sem recalcular).

    `optimized_pool` aceita a lista de cartas ou o dict retornado por
    `enforce_budget_and_curve` (com metadados). A lista embutida já vem
    agrupada (Counter) no formato Nx Nome.
    """
    if isinstance(optimized_pool, dict) and "deck" in optimized_pool:
        deck = list(optimized_pool.get("deck") or [])
        total_price = optimized_pool.get("total_price")
        curve = optimized_pool.get("curve_achieved") or _curve_achieved(deck)
        shortfall = optimized_pool.get(
            "spell_shortfall", optimized_pool.get("shortfall", 0)
        )
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

    deck_block = format_decklist_export(deck, commander_name=commander_name).rstrip()
    if not deck_block:
        deck_block = "(lista vazia)"

    curve_lines = ", ".join(f"CMC{b}={curve.get(b, 0)}" for b in range(8))
    target_lines = (
        ", ".join(f"CMC{b}={target.get(b, 0)}" for b in range(8)) if target else "N/A"
    )
    request = (user_request or "").strip() or "(sem pedido adicional)"

    return f"""## DECKLIST OFICIAL (GERADA EM PYTHON — NÃO ALTERAR)
Comandante: {commander_name}
Cartas no deck (99-slot, sem o comandante): {len(deck)}
Orçamento total (BRL, soma LigaMagic): R$ {total_price}
Curva alcançada: {curve_lines}
Curva-alvo: {target_lines}
Shortfall de magias: {shortfall}

### Pedido do jogador
{request}

### Lista (formato importação — já agrupada com Counter)
{deck_block}

## INSTRUÇÕES AO APRESENTADOR
1. Reproduza a lista acima SEM alterar nomes, quantidades nem ordem das categorias.
2. Mantenha o formato "Nx Nome" e cabeçalhos "# Categoria"; NÃO adicione prosa nas linhas.
3. NÃO recalcule orçamento, curva de mana nem identidade de cor — os números acima são finais.
4. Fora do bloco da lista, uma frase curta de abertura é opcional; a lista em si deve ser crua.
"""


# ---------------------------------------------------------------------------
# Tools do Deckbuilder agentic (function calling)
# ---------------------------------------------------------------------------

DECKBUILDER_TOOLS: list[dict[str, Any]] = [
    {
        "name": "lookup_card_prices",
        "description": (
            "Consulta preços em R$ (BRL). Preferência LigaMagic; se não houver "
            "preço local, estima BRL internamente. Sempre fale em R$ com o "
            "jogador — nunca mencione USD/dólar/Scryfall. Cartas sem preço "
            "retornam found=false. Use com orçamento definido."
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
            "Soma o preço de um pacote em R$ (LigaMagic) e compara com o "
            "orçamento máximo do Passo 0. Cartas sem BRL entram em missing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "card_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Nomes das cartas do pacote.",
                },
                "max_budget_brl": {
                    "type": "number",
                    "description": (
                        "Orçamento máximo em reais (R$). Omita ou null se sem limite."
                    ),
                },
            },
            "required": ["card_names"],
        },
    },
]


def build_autopilot_deck(
    commander_name: str,
    bracket: int,
    max_budget_brl: float,
    *,
    pool_size: int = 400,
) -> dict[str, Any]:
    """Gera lista Commander completa via motor Python (sem micro-passos LLM).

    Orçamento em BRL (LigaMagic). Lands e magias em quotas separadas; falha se
    o shortfall de magias for alto demais (não entrega 80 basics como sucesso).
    """
    name = (commander_name or "").strip()
    if not name:
        return {"ok": False, "error": "Informe o nome do comandante."}

    try:
        bracket_n = int(bracket)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Bracket inválido (use 1 a 5)."}
    if bracket_n not in BRACKET_CURVES:
        return {"ok": False, "error": "Bracket deve ser entre 1 e 5."}

    try:
        budget = float(max_budget_brl)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Orçamento inválido."}
    if budget < 0:
        return {"ok": False, "error": "Orçamento não pode ser negativo."}

    commander = fetch_commander(name)
    if commander is None:
        return {"ok": False, "error": f"Comandante não encontrado no Scryfall: {name}"}

    ci = commander.get("color_identity") or []
    pool = fetch_commander_card_pool(commander["name"], max_cards=pool_size)
    legal = filter_pool_for_commander(pool, ci)
    priced = enrich_cards_with_ligamagic_brl(legal)
    priced_count = sum(
        1 for c in priced if (c.get("prices") or {}).get("brl") is not None
    )
    priced_spells = sum(
        1
        for c in priced
        if not _is_land(c) and (c.get("prices") or {}).get("brl") is not None
    )
    optimized = enforce_budget_and_curve(
        priced,
        budget,
        BRACKET_CURVES[bracket_n],
        commander_colors=ci,
    )

    spell_shortfall = int(optimized.get("spell_shortfall") or 0)
    spell_count = int(optimized.get("spell_count") or 0)
    spell_slots = int(optimized.get("spell_slots") or (DECK_SIZE - 36))

    if spell_shortfall > MAX_SPELL_SHORTFALL:
        if priced_spells >= spell_slots - MAX_SPELL_SHORTFALL:
            error = (
                f"Orçamento R$ {budget:.0f} apertado demais: só couberam "
                f"{spell_count} de {spell_slots} magias (faltam {spell_shortfall}), "
                f"mesmo com {priced_spells} magias precificadas no pool. "
                f"Aumente o orçamento ou use o chat em micro-passos."
            )
        else:
            error = (
                f"Poucas cartas com preço utilizável: só {spell_count} magias de "
                f"{spell_slots} slots (faltam {spell_shortfall}). "
                f"Precificadas no pool: {priced_count} ({priced_spells} magias). "
                f"Tente de novo mais tarde ou use o chat em micro-passos."
            )
        return {
            "ok": False,
            "error": error,
            "commander": commander,
            "bracket": bracket_n,
            "max_budget_brl": budget,
            "priced_count": priced_count,
            "priced_spells": priced_spells,
            "optimized": optimized,
            "pool_size": len(pool),
            "legal_size": len(legal),
        }

    export = format_decklist_export(
        optimized["deck"], commander_name=commander["name"]
    )
    warning = None
    if optimized.get("degraded") or int(optimized.get("spell_slot_basics") or 0) > 0:
        warning = (
            f"Lista degradada: faltaram {spell_shortfall} magias com preço BRL; "
            f"{optimized.get('spell_slot_basics', 0)} slots foram preenchidos com "
            f"básicos. Roles: {optimized.get('role_counts') or {}}."
        )
    return {
        "ok": True,
        "commander": commander,
        "bracket": bracket_n,
        "max_budget_brl": budget,
        "currency": "BRL",
        "price_source": "ligamagic",
        "priced_count": priced_count,
        "priced_spells": priced_spells,
        "optimized": optimized,
        "export": export,
        "pool_size": len(pool),
        "legal_size": len(legal),
        "spell_count": spell_count,
        "land_count": int(optimized.get("land_count") or 0),
        "spell_shortfall": spell_shortfall,
        "land_shortfall": int(optimized.get("land_shortfall") or 0),
        "total_brl": float(optimized.get("total_price") or 0),
        "degraded": bool(optimized.get("degraded")),
        "warning": warning,
    }


def lookup_card_prices(card_names: list[str]) -> list[dict[str, Any]]:
    """Precifica cartas em BRL: LigaMagic, senão USD Scryfall × taxa fixa.

    A resposta ao LLM/UI fica só em R$ (sem campo USD). Fonte interna
    `estimated` = conversão provisória — não mencionar dólar ao jogador.
    """
    from .config import resolve_brl_price
    from .ligamagic_prices import fetch_ligamagic_brl

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
                    "brl": None,
                    "price": None,
                    "currency": "BRL",
                    "source": None,
                    "found": False,
                }
            )
            continue

        liga = fetch_ligamagic_brl(info["name"])
        brl, source = resolve_brl_price(ligamagic_brl=liga, usd=info.get("usd"))
        found = brl is not None
        results.append(
            {
                "query": query,
                "name": info["name"],
                "mana_cost": info.get("mana_cost", ""),
                "brl": brl,
                "price": brl,
                "currency": "BRL",
                "source": source if found else None,
                "found": found,
            }
        )
    return results


def summarize_package_budget(
    card_names: list[str], max_budget_brl: float | None = None
) -> dict[str, Any]:
    """Soma preços do pacote em R$ (LigaMagic) e valida orçamento."""
    priced = lookup_card_prices(card_names)
    total_brl = 0.0
    missing: list[str] = []
    priced_cards: list[dict[str, Any]] = []
    for item in priced:
        if not item["found"] or item.get("price") is None:
            missing.append(item["name"])
            continue
        priced_cards.append(item)
        total_brl += float(item["price"])

    budget: float | None
    if max_budget_brl is None:
        budget = None
        within = True
    else:
        try:
            budget = float(max_budget_brl)
        except (TypeError, ValueError):
            budget = None
            within = True
        else:
            within = total_brl <= budget + 1e-9

    return {
        "total": round(total_brl, 2),
        "currency": "BRL",
        "total_brl": round(total_brl, 2),
        "max_budget_brl": budget,
        "max_budget": budget,
        "within_budget": within,
        "missing": missing,
        "cards": priced_cards,
        "card_count_priced": len(priced_cards),
        "priced_brl": len(priced_cards),
        "price_source": "ligamagic",
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
            # Aceita max_budget_brl; alias legado max_budget_usd (mesmo valor em R$).
            max_budget = args.get("max_budget_brl", args.get("max_budget_usd", None))
            payload = summarize_package_budget(
                [str(n) for n in names], max_budget_brl=max_budget
            )
            return json.dumps(payload, ensure_ascii=False)
        return json.dumps({"error": f"Tool desconhecida: {name}"}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
