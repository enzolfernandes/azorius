"""Parser de decklists no formato Azorius / Moxfield-lite.

Módulo puro: detecta cabeçalhos `# Categoria` e linhas `Nx Nome [...]`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HEADER_RE = re.compile(r"^#\s+(.+?)\s*$")
# 1x Sol Ring | 2x Path to Exile (cmm) [Removal]
CARD_LINE_RE = re.compile(
    r"^\s*(\d+)\s*[xX]\s+(.+?)\s*$"
)


@dataclass
class DecklistBlock:
    title: str
    lines: list[str] = field(default_factory=list)
    cards: list[tuple[int, str]] = field(default_factory=list)  # (qty, name)


@dataclass
class ParsedDeckMessage:
    """Resultado do parse de uma mensagem do assistente."""

    prose_before: str
    blocks: list[DecklistBlock]
    prose_after: str
    raw: str

    @property
    def has_structured_list(self) -> bool:
        return bool(self.blocks)


def parse_card_line(line: str) -> tuple[int, str] | None:
    """Extrai (quantidade, nome) de uma linha Nx Nome; None se inválida."""
    match = CARD_LINE_RE.match(line.strip())
    if not match:
        return None
    qty = int(match.group(1))
    rest = match.group(2).strip()
    # Remove tags [Removal] e edições (cmm) do nome canônico de busca.
    name = re.sub(r"\s*\[[^\]]*\]\s*", " ", rest)
    name = re.sub(r"\s*\([^)]*\)\s*", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    if qty < 1 or not name:
        return None
    return qty, name


def parse_decklist_text(text: str) -> ParsedDeckMessage:
    """Parte texto em prosa + blocos `# Header` com linhas de carta."""
    raw = text or ""
    lines = raw.splitlines()
    blocks: list[DecklistBlock] = []
    current: DecklistBlock | None = None
    before: list[str] = []
    after: list[str] = []
    seen_block = False

    for line in lines:
        header = HEADER_RE.match(line.strip())
        if header:
            seen_block = True
            if current is not None:
                blocks.append(current)
            current = DecklistBlock(title=header.group(1).strip())
            continue

        card = parse_card_line(line)
        if current is not None and card is not None:
            qty, name = card
            current.lines.append(line.rstrip())
            current.cards.append((qty, name))
            continue

        if current is not None and line.strip() == "":
            # Linha em branco encerra o bloco atual.
            blocks.append(current)
            current = None
            continue

        if current is not None and card is None and line.strip():
            # Texto solto dentro de um bloco — encerra bloco e vai para prosa.
            blocks.append(current)
            current = None
            after.append(line)
            continue

        if not seen_block:
            before.append(line)
        else:
            after.append(line)

    if current is not None:
        blocks.append(current)

    # Só considera estruturado se houver ao menos um bloco com cartas.
    blocks = [b for b in blocks if b.cards]
    return ParsedDeckMessage(
        prose_before="\n".join(before).strip(),
        blocks=blocks,
        prose_after="\n".join(after).strip(),
        raw=raw,
    )


def parse_pasted_decklist(text: str) -> list[tuple[int, str]]:
    """Normaliza lista colada (Nx Nome ou Nomes soltos = 1x)."""
    entries: list[tuple[int, str]] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parsed = parse_card_line(stripped)
        if parsed:
            entries.append(parsed)
            continue
        # Nome cru sem quantidade.
        if not stripped.startswith("-") and len(stripped) > 1:
            name = re.sub(r"^\d+\s+", "", stripped).strip()
            if name:
                entries.append((1, name))
    return entries
