"""Persistência local de conversas (Juiz / Deckbuilder).

Armazena JSON em `data/conversations/`. Módulo puro — a UI só orquestra.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from services.config import DATA_DIR

CONVERSATIONS_DIR = DATA_DIR / "conversations"

MODE_JUDGE = "judge"
MODE_DECKBUILDER = "deckbuilder"


@dataclass
class Conversation:
    id: str
    mode: str
    title: str
    messages: list[dict] = field(default_factory=list)
    card_history: list[dict] = field(default_factory=list)
    assembled_deck: list[dict] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dir() -> Path:
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    return CONVERSATIONS_DIR


def _path_for(conversation_id: str) -> Path:
    return ensure_dir() / f"{conversation_id}.json"


def new_conversation(mode: str, title: str = "Nova conversa") -> Conversation:
    now = _now_iso()
    return Conversation(
        id=uuid.uuid4().hex[:12],
        mode=mode,
        title=title[:80] or "Nova conversa",
        messages=[],
        card_history=[],
        assembled_deck=[],
        created_at=now,
        updated_at=now,
    )


def save_conversation(conversation: Conversation) -> Path:
    conversation.updated_at = _now_iso()
    path = _path_for(conversation.id)
    path.write_text(
        json.dumps(conversation.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_conversation(conversation_id: str) -> Conversation | None:
    path = _path_for(conversation_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw_cards = data.get("card_history") or []
    cards: list[dict] = [c for c in raw_cards if isinstance(c, dict) and c.get("name")]
    raw_deck = data.get("assembled_deck") or []
    deck: list[dict] = [
        e
        for e in raw_deck
        if isinstance(e, dict) and e.get("name") and int(e.get("qty") or 0) > 0
    ]
    return Conversation(
        id=data.get("id", conversation_id),
        mode=data.get("mode", MODE_JUDGE),
        title=data.get("title", "Conversa"),
        messages=list(data.get("messages") or []),
        card_history=cards,
        assembled_deck=deck,
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
    )


def list_conversations(mode: str | None = None) -> list[Conversation]:
    ensure_dir()
    items: list[Conversation] = []
    for path in CONVERSATIONS_DIR.glob("*.json"):
        conv = load_conversation(path.stem)
        if conv is None:
            continue
        if mode and conv.mode != mode:
            continue
        items.append(conv)
    items.sort(key=lambda c: c.updated_at or c.created_at, reverse=True)
    return items


def delete_conversation(conversation_id: str) -> bool:
    path = _path_for(conversation_id)
    if not path.is_file():
        return False
    path.unlink()
    return True


def title_from_messages(messages: list[dict], fallback: str = "Conversa") -> str:
    for msg in messages:
        if msg.get("role") == "user" and msg.get("content"):
            text = str(msg["content"]).strip().replace("\n", " ")
            return (text[:60] + "…") if len(text) > 60 else text or fallback
    return fallback
