"""Template de composição Commander — fonte única de faixas por pacote.

Usado por prompts (llm_engine), auditoria de upgrade e quotas do autopilot.
"""

from __future__ import annotations

from typing import Any

# Pacotes medidos no template (99 cartas + comandante fora da lista).
PACKAGE_RANGES: dict[str, tuple[int, int]] = {
    "lands": (35, 38),
    "ramp": (10, 12),
    "draw": (10, 10),
    "removal": (10, 12),
    "board_wipe": (3, 4),
    "synergy": (25, 30),
}

PACKAGE_LABELS_PT: dict[str, str] = {
    "lands": "Terrenos",
    "ramp": "Aceleração de Mana (Ramp)",
    "draw": "Compra de Cartas (Card Draw)",
    "removal": "Remoções Pontuais",
    "board_wipe": "Limpezas de Mesa (Board Wipes)",
    "synergy": "Cartas de Sinergia/Tema Principal",
}

PACKAGE_HINTS_PT: dict[str, str] = {
    "lands": "",
    "ramp": "pedras de mana, dorks, feitiços que buscam terrenos",
    "draw": "",
    "removal": "misturando destruição de criaturas, artefatos e encantamentos",
    "board_wipe": "",
    "synergy": "tribo, combos, wincons e estratégias do comandante",
}

# Ordem de preenchimento no autopilot (após roles estruturais, sinergia).
SPELL_ROLE_FILL_ORDER: tuple[str, ...] = (
    "ramp",
    "draw",
    "removal",
    "board_wipe",
    "synergy",
)

LAND_SLOTS_MIN = PACKAGE_RANGES["lands"][0]
LAND_SLOTS_MAX = PACKAGE_RANGES["lands"][1]

# Contagens legadas / aliases que somam no pacote de sinergia na auditoria.
_SYNERGY_ALIASES = frozenset({"synergy", "other", "protection"})


def format_range(lo: int, hi: int) -> str:
    if lo == hi:
        return str(lo)
    return f"{lo}–{hi}"


def mid_target(package: str) -> int:
    """Alvo intermediário (autopilot / display)."""
    lo, hi = PACKAGE_RANGES[package]
    return (lo + hi) // 2


def package_mins() -> dict[str, int]:
    """Mínimo de cada pacote do template (limiar de gap / quota)."""
    return {pkg: lo for pkg, (lo, _hi) in PACKAGE_RANGES.items()}


def spell_role_mins() -> dict[str, int]:
    """Mínimos só das magias (sem lands) — ordem em SPELL_ROLE_FILL_ORDER."""
    return {role: PACKAGE_RANGES[role][0] for role in SPELL_ROLE_FILL_ORDER}


def format_template_for_prompt() -> str:
    """Bloco de texto para system prompts do Deckbuilder."""
    lines = [
        "TEMPLATE DE COMPOSIÇÃO OBRIGATÓRIO (99 cartas no deck; comandante à parte):"
    ]
    for pkg, (lo, hi) in PACKAGE_RANGES.items():
        label = PACKAGE_LABELS_PT[pkg]
        hint = PACKAGE_HINTS_PT.get(pkg) or ""
        range_txt = format_range(lo, hi)
        if hint:
            lines.append(f"- {label}: {range_txt} ({hint})")
        else:
            lines.append(f"- {label}: {range_txt}")
    lines.append(
        "Feche cada pacote dentro da faixa; o restante dos slots até 99 "
        "ajusta sinergia/tema ou terrenos no topo da faixa."
    )
    return "\n".join(lines)


def effective_count(package: str, counts: dict[str, int]) -> int:
    """Contagem efetiva: sinergia inclui other/protection legados."""
    if package == "synergy":
        return sum(int(counts.get(k, 0)) for k in _SYNERGY_ALIASES)
    return int(counts.get(package, 0))


def gaps_from_counts(counts: dict[str, int]) -> list[dict[str, Any]]:
    """Déficits vs mínimo da faixa. `target` = mínimo; `target_label` = faixa."""
    gaps: list[dict[str, Any]] = []
    for pkg, (lo, hi) in PACKAGE_RANGES.items():
        have = effective_count(pkg, counts)
        if have < lo:
            gaps.append(
                {
                    "package": pkg,
                    "have": have,
                    "target": lo,
                    "target_min": lo,
                    "target_max": hi,
                    "target_label": format_range(lo, hi),
                    "deficit": lo - have,
                }
            )
    return gaps
