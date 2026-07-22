"""Interface Streamlit do Juiz / Deckbuilder de MTG.

Este arquivo é EXCLUSIVAMENTE camada de apresentação: toda a lógica de negócio
vive em /services. Aqui apenas orquestramos as chamadas (injetando o provedor
de IA nos services que precisam dele) e traduzimos erros em mensagens de UI.
"""

from __future__ import annotations

import itertools
import re

import streamlit as st
import streamlit.components.v1 as components

from services.config import (
    CHROMA_DIR,
    VALID_PROVIDERS,
    ConfigError,
    api_key_from_env,
    load_ui_defaults,
    persisted_api_key,
    resolve_brl_price,
    save_ui_settings,
    settings_from_values,
)
from services.conversations import (
    MODE_DECKBUILDER as CONV_DECK,
    MODE_JUDGE as CONV_JUDGE,
    delete_conversation,
    list_conversations,
    load_conversation,
    new_conversation,
    save_conversation,
    title_from_messages,
)
from services.card_preview import (
    deck_rows_html,
    estimate_deck_list_height,
    estimate_html_height,
    format_brl_label,
    linkify_known_cards,
    strip_hover_attrs,
    wrap_preview_document,
)
from services.deck_assembly import (
    extract_entries_from_text,
    format_export,
    group_by_section,
    merge_entries,
    rebuild_from_messages,
    total_cards,
)
from services.deck_engine import build_autopilot_deck
from services.deck_upgrade import build_upgrade_brief
from services.decklist_parse import parse_decklist_text
from services.ligamagic_prices import clear_last_warning, fetch_ligamagic_brl, get_last_warning
from services.mana_symbols import (
    MANA_SYMBOL_CSS,
    has_mana_symbols,
    replace_mana_symbols,
    replace_mana_symbols_escaped,
)
from services.llm_engine import (
    DECKBUILDER_BOOTSTRAP_MESSAGE,
    chat_deckbuilder,
    chat_deckbuilder_upgrade,
    extract_card_names_from_answer,
    extract_card_names_llm,
    generate_judge_ruling,
    narrate_autopilot_deck,
    rewrite_rules_query,
)
from services.providers import ProviderError, get_provider
from services.scryfall_api import fetch_card_data, fetch_card_image_url, fetch_card_market_info
from services.vector_db import VectorDBError, initialize_db, query_rules

# Convenção da comunidade MTG: cartas citadas entre [[colchetes duplos]].
CARD_MENTION_PATTERN = re.compile(r"\[\[([^\[\]]+)\]\]")

MODE_JUDGE = "Modo Juiz"
MODE_DECKBUILDER = "Modo Deckbuilder"
_LEGACY_MODE_SETTINGS = "Configurações"
_BASIC_PRICE_ZERO = frozenset(
    {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}
)

st.set_page_config(page_title="Azorius — MTG", page_icon="⚖️", layout="wide")


@st.cache_resource(show_spinner="Inicializando o banco de regras (primeira execução demora)...")
def get_collection(provider_name: str, _api_key: str):
    settings = settings_from_values(provider_name, _api_key)
    embed_provider = get_provider(settings)
    persist_dir = CHROMA_DIR / provider_name
    return initialize_db(embed_fn=embed_provider.embed, persist_dir=persist_dir)


def extract_card_names(text: str) -> list[str]:
    """Extrai nomes de cartas citados como [[Nome da Carta]]."""
    seen: list[str] = []
    for name in CARD_MENTION_PATTERN.findall(text):
        cleaned = name.strip()
        if cleaned and cleaned.lower() not in (s.lower() for s in seen):
            seen.append(cleaned)
    return seen


def _ensure_session_defaults() -> None:
    if "app_mode" not in st.session_state:
        st.session_state.app_mode = MODE_JUDGE
    # Sessões antigas tinham Configurações como modo do radio.
    if st.session_state.app_mode == _LEGACY_MODE_SETTINGS:
        st.session_state.app_mode = MODE_JUDGE
    if "_prev_app_mode" not in st.session_state:
        st.session_state._prev_app_mode = st.session_state.app_mode
    if st.session_state._prev_app_mode == _LEGACY_MODE_SETTINGS:
        st.session_state._prev_app_mode = st.session_state.app_mode
    if "llm_provider" not in st.session_state or "api_key" not in st.session_state:
        default_provider, default_key = load_ui_defaults()
        st.session_state.llm_provider = default_provider
        st.session_state.api_key = default_key
    if "judge_messages" not in st.session_state:
        st.session_state.judge_messages = []
    if "deckbuilder_messages" not in st.session_state:
        st.session_state.deckbuilder_messages = []
    if "card_history" not in st.session_state:
        st.session_state.card_history = []
    if "assembled_deck" not in st.session_state:
        st.session_state.assembled_deck = []
    if "deck_submode" not in st.session_state:
        st.session_state.deck_submode = "Criar do zero"
    if "judge_conversation_id" not in st.session_state:
        st.session_state.judge_conversation_id = None
    if "deck_conversation_id" not in st.session_state:
        st.session_state.deck_conversation_id = None
    if "pending_chat_submit" not in st.session_state:
        st.session_state.pending_chat_submit = None
    if "app_ready" not in st.session_state:
        # Só libera Juiz/Deckbuilder após confirmação explícita nesta sessão.
        st.session_state.app_ready = False


def _bootstrap_deckbuilder_chat() -> None:
    st.session_state.deckbuilder_messages = [
        {"role": "assistant", "content": DECKBUILDER_BOOTSTRAP_MESSAGE}
    ]
    st.session_state.deck_conversation_id = None
    st.session_state.assembled_deck = []


def _session_settings_valid() -> bool:
    try:
        settings_from_values(
            st.session_state.llm_provider, st.session_state.api_key
        )
        return True
    except ConfigError:
        return False


def _apply_settings(provider: str, api_key: str) -> None:
    """Grava sessão + disco e libera o motor (Juiz/Deckbuilder)."""
    st.session_state.llm_provider = provider
    st.session_state.api_key = api_key.strip()
    save_ui_settings(provider, api_key.strip())
    st.session_state.app_ready = True


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def cached_card_image_url(name: str) -> str | None:
    """URL de imagem Scryfall com cache de sessão/disco do Streamlit."""
    return fetch_card_image_url(name)


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def cached_card_brl(name: str) -> float | None:
    """Preço BRL (LigaMagic preferencial; estimado só se faltar); básicos = 0."""
    cleaned = (name or "").strip()
    if not cleaned:
        return None
    if cleaned in _BASIC_PRICE_ZERO:
        return 0.0
    liga = fetch_ligamagic_brl(cleaned)
    info = fetch_card_market_info(cleaned)
    usd = info.get("usd") if info else None
    brl, _source = resolve_brl_price(ligamagic_brl=liga, usd=usd)
    return brl


def _render_priced_rows(
    rows: list[dict],
    *,
    show_total: bool = False,
    total_brl: float | None = None,
    missing_prices: int = 0,
) -> None:
    """Lista HTML: nome com hover de arte + preço (iframe compacto)."""
    body = deck_rows_html(
        rows,
        show_total=show_total,
        total_brl=total_brl,
        missing_prices=missing_prices,
    )
    height = estimate_deck_list_height(len(rows), with_total=show_total)
    _render_hover_html(
        body,
        height=height,
        enable_hover=True,
        scrolling=len(rows) > 12,
    )


def _priced_deck_rows(cards: list[tuple[int, str]] | list[dict]) -> tuple[list[dict], float, int]:
    """Monta linhas {qty,name,image_url,brl,line_brl} + total + qtd sem preço."""
    rows: list[dict] = []
    total = 0.0
    missing = 0
    for item in cards:
        if isinstance(item, dict):
            qty = int(item.get("qty") or 1)
            name = (item.get("name") or "").strip()
        else:
            qty, name = int(item[0]), str(item[1]).strip()
        if not name or qty < 1:
            continue
        unit = cached_card_brl(name)
        if unit is None:
            missing += 1
            line = None
        else:
            line = float(unit) * qty
            total += line
        rows.append(
            {
                "qty": qty,
                "name": name,
                "image_url": cached_card_image_url(name),
                "brl": unit,
                "line_brl": line,
            }
        )
    return rows, round(total, 2), missing


def _render_settings_form(*, submit_label: str) -> bool:
    """Formulário compartilhado (gate + dialog). True se o usuário submeteu."""
    provider_options = list(VALID_PROVIDERS)
    try:
        default_index = provider_options.index(st.session_state.llm_provider)
    except ValueError:
        default_index = 0

    if "ui_llm_provider" not in st.session_state:
        st.session_state.ui_llm_provider = provider_options[default_index]

    provider = st.selectbox(
        "Provedor de LLM",
        options=provider_options,
        key="ui_llm_provider",
    )
    key_label = {
        "gemini": "Google API Key",
        "openai": "OpenAI API Key",
        "claude": "Anthropic API Key",
    }.get(provider, "API Key")
    widget_key = f"ui_api_key_{provider}"
    if widget_key not in st.session_state:
        if provider == st.session_state.llm_provider and st.session_state.api_key:
            st.session_state[widget_key] = st.session_state.api_key
        else:
            st.session_state[widget_key] = (
                persisted_api_key(provider) or api_key_from_env(provider)
            )

    api_key = st.text_input(
        key_label,
        type="password",
        key=widget_key,
        help="Ao confirmar, provedor e chave são lembrados em data/ui_settings.json (local).",
    )

    st.caption(
        f"Pré-preenchido: `{provider}` · "
        f"chave {'presente' if api_key.strip() else 'ausente'}"
    )

    submitted = st.button(submit_label, type="primary", use_container_width=True)
    if submitted:
        try:
            settings_from_values(provider, api_key)
        except ConfigError as exc:
            st.error(str(exc))
            return False
        _apply_settings(provider, api_key)
        return True
    return False


@st.dialog("Configurações")
def open_settings_dialog() -> None:
    """Popup de provedor/chave — mesma aparência na entrada e depois de iniciar."""
    st.caption(
        "Altere provedor e chave. Ao aplicar, Juiz e Deckbuilder usam os novos valores."
    )
    if _render_settings_form(submit_label="Aplicar"):
        st.rerun()
    st.caption(
        "As preferências ficam em `data/ui_settings.json` (não versionado). "
        "O `.env` continua como fallback."
    )


def _render_gate_shell() -> None:
    """Shell visual igual ao app (sidebar + área principal) por trás do dialog."""
    # Primeira pintura da entrada espelha a captura (Deckbuilder + geração rápida).
    if not st.session_state.get("_gate_bootstrapped"):
        st.session_state.app_mode = MODE_DECKBUILDER
        st.session_state.deck_submode = "Geração rápida"
        st.session_state._gate_bootstrapped = True

    with st.sidebar:
        st.header("Modo")
        st.radio(
            "Escolha o modo",
            options=[MODE_JUDGE, MODE_DECKBUILDER],
            key="app_mode",
            label_visibility="collapsed",
        )
        if st.button("⚙️ Configurações", use_container_width=True, key="gate_settings_btn"):
            st.session_state._open_setup_dialog = True

        st.header("Conversas")
        st.button(
            "Nova conversa",
            use_container_width=True,
            disabled=True,
            key="gate_new_conv",
        )
        st.caption("Nenhuma conversa salva ainda.")

        if st.session_state.app_mode == MODE_DECKBUILDER:
            st.header("Lista do deck")
            st.caption(
                "Conforme o assistente sugerir pacotes, a lista aparece aqui "
                "para download."
            )

    if st.session_state.app_mode == MODE_DECKBUILDER:
        st.title("🛠️ Deckbuilder Azorius")
        st.caption(
            "Oficina de Commander: chat em micro-passos, geração rápida (motor Python) "
            "ou melhoria de lista colada. A sidebar acumula a lista do deck. "
            "Orçamento em R$ via LigaMagic (mercado Brasil)."
        )
        if "gate_deck_submode_preview" not in st.session_state:
            st.session_state.gate_deck_submode_preview = "Geração rápida"
        st.radio(
            "Fluxo",
            options=["Criar do zero", "Melhorar lista", "Geração rápida"],
            horizontal=True,
            key="gate_deck_submode_preview",
            disabled=True,
        )
        st.subheader("Geração rápida (autopilot)")
        st.caption(
            "Motor Python: comandante → pool → identidade → budget LigaMagic (R$)/curva → lista. "
            "Sem micro-confirmações dos Passos 1–5. A primeira geração pode demorar "
            "(consulta de preços)."
        )
        with st.form("gate_autopilot_preview"):
            st.text_input("Comandante", value="Niv-Mizzet, Parun", disabled=True)
            st.select_slider(
                "Bracket", options=[1, 2, 3, 4, 5], value=3, disabled=True
            )
            st.number_input(
                "Orçamento máximo (R$ — LigaMagic)",
                min_value=0.0,
                value=800.0,
                step=50.0,
                disabled=True,
            )
            st.form_submit_button("Gerar deck", type="primary", disabled=True)
        st.info(DECKBUILDER_BOOTSTRAP_MESSAGE)
    else:
        st.title("⚖️ Juiz Azorius")
        st.caption(
            "Tire dúvidas de regras com base nas Comprehensive Rules. "
            "Aplique as Configurações para começar."
        )
        st.chat_input("Configure o provedor para perguntar…", disabled=True)


def render_setup_gate() -> None:
    """Entrada: shell do app + dialog Configurações (mesma aparência da captura)."""
    _ensure_session_defaults()
    if "_open_setup_dialog" not in st.session_state:
        st.session_state._open_setup_dialog = True

    _render_gate_shell()

    # Mantém o popup aberto até Aplicar (reabre se o usuário fechar sem salvar).
    if st.session_state._open_setup_dialog or not st.session_state.app_ready:
        open_settings_dialog()
        st.session_state._open_setup_dialog = False


def render_mode_sidebar() -> str:
    """Seletor de modo + histórico + botão de Configurações (dialog)."""
    _ensure_session_defaults()

    with st.sidebar:
        st.header("Modo")
        st.radio(
            "Escolha o modo",
            options=[MODE_JUDGE, MODE_DECKBUILDER],
            key="app_mode",
            label_visibility="collapsed",
        )
        if st.button("⚙️ Configurações", use_container_width=True):
            open_settings_dialog()

        mode = st.session_state.app_mode
        if mode == MODE_JUDGE:
            _render_conversation_sidebar(CONV_JUDGE, "judge")
        elif mode == MODE_DECKBUILDER:
            _render_conversation_sidebar(CONV_DECK, "deck")

    current = st.session_state.app_mode
    previous = st.session_state._prev_app_mode
    if current == MODE_DECKBUILDER and previous != MODE_DECKBUILDER:
        if not st.session_state.deckbuilder_messages:
            _bootstrap_deckbuilder_chat()
    elif current == MODE_DECKBUILDER and not st.session_state.deckbuilder_messages:
        _bootstrap_deckbuilder_chat()
    st.session_state._prev_app_mode = current
    return current


def _render_conversation_sidebar(conv_mode: str, prefix: str) -> None:
    st.header("Conversas")
    id_key = f"{prefix}_conversation_id"
    messages_key = "judge_messages" if prefix == "judge" else "deckbuilder_messages"

    if st.button("Nova conversa", key=f"{prefix}_new_conv", use_container_width=True):
        if prefix == "deck":
            _bootstrap_deckbuilder_chat()
        else:
            st.session_state.judge_messages = []
            st.session_state[id_key] = None
            st.session_state.card_history = []
        st.rerun()

    items = list_conversations(conv_mode)
    if not items:
        st.caption("Nenhuma conversa salva ainda.")
        return

    for conv in items[:30]:
        label = conv.title or "Conversa"
        active = st.session_state.get(id_key) == conv.id
        cols = st.columns([4, 1])
        with cols[0]:
            if st.button(
                ("● " if active else "") + label,
                key=f"{prefix}_open_{conv.id}",
                use_container_width=True,
            ):
                loaded = load_conversation(conv.id)
                if loaded:
                    st.session_state[messages_key] = list(loaded.messages)
                    st.session_state[id_key] = loaded.id
                    if prefix == "judge":
                        st.session_state.card_history = list(
                            loaded.card_history or []
                        )
                        st.session_state["_hydrate_cards"] = not bool(
                            loaded.card_history
                        )
                    else:
                        deck = list(loaded.assembled_deck or [])
                        if not deck and loaded.messages:
                            deck = rebuild_from_messages(loaded.messages)
                        st.session_state.assembled_deck = deck
                    st.rerun()
        with cols[1]:
            if st.button("✕", key=f"{prefix}_del_{conv.id}", help="Apagar"):
                delete_conversation(conv.id)
                if st.session_state.get(id_key) == conv.id:
                    st.session_state[id_key] = None
                    if prefix == "deck":
                        _bootstrap_deckbuilder_chat()
                    else:
                        st.session_state.judge_messages = []
                        st.session_state.card_history = []
                st.rerun()


def _persist_messages(prefix: str, conv_mode: str, messages: list[dict]) -> None:
    id_key = f"{prefix}_conversation_id"
    conv_id = st.session_state.get(id_key)
    if conv_id:
        conv = load_conversation(conv_id)
        if conv is None:
            conv = new_conversation(conv_mode)
            st.session_state[id_key] = conv.id
    else:
        conv = new_conversation(conv_mode)
        st.session_state[id_key] = conv.id
    conv.messages = list(messages)
    conv.title = title_from_messages(messages, fallback=conv.title)
    conv.mode = conv_mode
    if conv_mode == CONV_JUDGE:
        conv.card_history = list(st.session_state.get("card_history") or [])
    else:
        conv.assembled_deck = list(st.session_state.get("assembled_deck") or [])
    save_conversation(conv)


def _ingest_assistant_deck_text(content: str) -> None:
    """Incorpora cartas da resposta do assistente na lista da sidebar."""
    entries = extract_entries_from_text(content)
    if not entries:
        return
    st.session_state.assembled_deck = merge_entries(
        st.session_state.get("assembled_deck") or [], entries
    )


def render_assembled_deck_sidebar(assembled: list[dict]) -> None:
    """Sidebar do Deckbuilder: lista acumulada com preço e hover de imagem."""
    with st.sidebar:
        st.header("Lista do deck")
        count = total_cards(assembled)
        if count == 0:
            st.caption(
                "Conforme o assistente sugerir pacotes (`Nx Nome`), "
                "a lista vai aparecendo aqui."
            )
            return

        rows, total_brl, missing = _priced_deck_rows(assembled)
        st.caption(
            f"{count} cartas · meta Commander ~99 + comandante · "
            f"Total {format_brl_label(total_brl)}"
            + (f" · {missing} sem preço" if missing else "")
        )
        export = format_export(assembled)
        st.download_button(
            "Baixar lista",
            data=export,
            file_name="azorius-deck.txt",
            mime="text/plain",
            use_container_width=True,
            key="deck_export_download",
        )
        with st.expander("Copiar (texto)", expanded=False):
            st.code(export, language=None)

        for section, cards in group_by_section(assembled):
            section_qty = sum(int(c["qty"]) for c in cards)
            section_rows, section_total, section_missing = _priced_deck_rows(cards)
            with st.expander(
                f"{section} ({section_qty}) · {format_brl_label(section_total)}",
                expanded=True,
            ):
                _render_priced_rows(
                    section_rows,
                    show_total=True,
                    total_brl=section_total,
                    missing_prices=section_missing,
                )

        st.markdown(f"**Total do deck: {format_brl_label(total_brl)}**")
        if missing:
            st.caption(f"{missing} carta(s) sem preço em R$.")


def _slim_card_for_storage(card: dict) -> dict:
    """Campos mínimos persistidos no JSON da conversa."""
    return {
        "name": card.get("name", ""),
        "mana_cost": card.get("mana_cost", ""),
        "type_line": card.get("type_line", ""),
        "oracle_text": card.get("oracle_text", ""),
        "image_url": card.get("image_url"),
    }


def update_card_history(new_cards: list[dict]) -> None:
    if not new_cards:
        return
    ordered: list[dict] = []
    seen: set[str] = set()
    for card in new_cards:
        key = (card.get("name") or "").lower()
        if not key or key in seen:
            continue
        ordered.append(_slim_card_for_storage(card))
        seen.add(key)
    history = [
        card
        for card in st.session_state.card_history
        if (card.get("name") or "").lower() not in seen
    ]
    st.session_state.card_history = ordered + history


def hydrate_card_history_from_messages(provider, messages: list[dict]) -> list[dict]:
    """Reconstrói histórico de cartas a partir do texto (conversas antigas)."""
    names: list[str] = []
    for msg in messages:
        content = msg.get("content") or ""
        names.extend(extract_card_names(content))
    blob = "\n\n".join(
        str(m.get("content") or "") for m in messages if m.get("content")
    )
    if blob.strip():
        try:
            names.extend(extract_card_names_from_answer(provider, blob))
        except ProviderError:
            pass
    # Dedup preservando ordem
    unique: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(name)
    resolved: list[dict] = []
    for name in unique[:40]:
        card = fetch_card_data(name, question=blob[:500] or name)
        if card:
            resolved.append(_slim_card_for_storage(card))
    return resolved


def render_cards_sidebar(cards: list[dict]) -> None:
    with st.sidebar:
        st.header("Histórico de cartas")
        if not cards:
            st.caption("Nenhuma carta citada ainda. Basta mencionar o nome na pergunta.")
            return
        for card in cards:
            if card.get("image_url"):
                st.image(card["image_url"], caption=card["name"], width="stretch")
            else:
                st.write(f"**{card['name']}** (sem imagem disponível)")


def resolve_cards(
    names: list[str],
    *,
    context: str,
    warn_missing: bool = False,
    skip_known: bool = True,
) -> tuple[list[dict], list[str]]:
    known = {card["name"].lower() for card in st.session_state.card_history}
    resolved: list[dict] = []
    missing: list[str] = []
    seen_resolved: set[str] = set()
    for name in names:
        if skip_known and name.lower() in known:
            continue
        card = fetch_card_data(name, question=context)
        if card is None:
            missing.append(name)
            if warn_missing:
                st.warning(f"Carta não encontrada no Scryfall: {name}")
            continue
        key = card["name"].lower()
        if key in seen_resolved or (skip_known and key in known):
            continue
        resolved.append(card)
        seen_resolved.add(key)
    return resolved, missing


def resolve_provider():
    try:
        settings = settings_from_values(
            st.session_state.llm_provider, st.session_state.api_key
        )
    except ConfigError as exc:
        st.session_state.app_ready = False
        st.warning(str(exc))
        st.stop()

    try:
        return get_provider(settings), settings
    except (ProviderError, ConfigError) as exc:
        st.session_state.app_ready = False
        st.error(str(exc))
        st.stop()


def _streamlit_theme_is_dark() -> bool:
    """Detecta tema escuro da app (iframe não herda cores sozinho)."""
    try:
        theme = getattr(st, "context", None)
        theme = getattr(theme, "theme", None) if theme is not None else None
        theme_type = getattr(theme, "type", None) if theme is not None else None
        if theme_type in ("dark", "light"):
            return theme_type == "dark"
    except Exception:
        pass
    try:
        base = st.get_option("theme.base")
        if base in ("dark", "light"):
            return base == "dark"
    except Exception:
        pass
    return True


def _render_hover_html(
    body_html: str, *, height: int, enable_hover: bool = True, scrolling: bool = True
) -> None:
    """Renderiza HTML com JS de preview (iframe components — hover confiável)."""
    components.html(
        wrap_preview_document(
            body_html,
            dark=_streamlit_theme_is_dark(),
            enable_hover=enable_hover,
        ),
        height=height,
        scrolling=scrolling,
    )





def _cards_for_linkify() -> list[dict]:
    """Cartas conhecidas da sessão + resolve imagem se faltar URL."""
    cards: list[dict] = []
    for card in st.session_state.get("card_history") or []:
        item = dict(card)
        name = (item.get("name") or "").strip()
        if not name:
            continue
        if not item.get("image_url"):
            item["image_url"] = cached_card_image_url(name)
        cards.append(item)
    return cards


def _render_chat_markdown(text: str, *, card_preview: bool = True) -> None:
    """Chat com ícones de mana e nomes marcados.

    `card_preview=False` na pergunta do usuário: marca o nome sem overlay
    (o iframe baixo da pergunta quebrava o layout com a imagem por cima).
    """
    if not text:
        return
    cards = _cards_for_linkify()
    mentions = [m.strip() for m in CARD_MENTION_PATTERN.findall(text) if m.strip()]
    needs_linkify = bool(cards) or bool(mentions)
    if needs_linkify:
        extra = list(cards)
        for name in mentions:
            if any(c.get("name", "").lower() == name.lower() for c in extra):
                continue
            extra.append({"name": name, "image_url": cached_card_image_url(name)})
        normalized = CARD_MENTION_PATTERN.sub(r"\1", text)
        body = linkify_known_cards(normalized, extra)
        if not card_preview:
            body = strip_hover_attrs(body)
        # Perguntas curtas: altura justa, sem folga de tip.
        height = estimate_html_height(
            text, min_h=56 if not card_preview else 100
        )
        _render_hover_html(body, height=height, enable_hover=card_preview)
        return
    if has_mana_symbols(text):
        st.html(MANA_SYMBOL_CSS)
        st.markdown(replace_mana_symbols(text), unsafe_allow_html=True)
    else:
        st.markdown(text)


def render_assistant_message(content: str, *, message_key: str) -> None:
    """Markdown / expanders `# Categoria` com hover de imagem e preços BRL."""
    del message_key
    parsed = parse_decklist_text(content)
    if not parsed.has_structured_list:
        _render_chat_markdown(content)
        return

    if parsed.prose_before:
        _render_chat_markdown(parsed.prose_before)

    deck_total = 0.0
    deck_missing = 0
    with st.spinner("Carregando imagens e preços (R$)..."):
        priced_blocks: list[tuple[str, list[dict], float, int]] = []
        for block in parsed.blocks:
            rows, section_total, section_missing = _priced_deck_rows(block.cards)
            priced_blocks.append(
                (block.title, rows, section_total, section_missing)
            )
            deck_total += section_total
            deck_missing += section_missing

    for title, rows, section_total, section_missing in priced_blocks:
        section_qty = sum(int(r["qty"]) for r in rows)
        with st.expander(
            f"{title} ({section_qty}) · {format_brl_label(section_total)}",
            expanded=True,
        ):
            _render_priced_rows(rows)

    st.markdown(f"**Total do deck: {format_brl_label(round(deck_total, 2))}**")
    if deck_missing:
        st.caption(
            f"{deck_missing} carta(s) sem preço em R$ (não entram no total)."
        )

    if parsed.prose_after:
        _render_chat_markdown(parsed.prose_after)


def _last_user_message(messages: list[dict]) -> str | None:
    for msg in reversed(messages):
        if msg.get("role") == "user" and msg.get("content"):
            return str(msg["content"])
    return None


def _chat_controls(mode_prefix: str, messages: list[dict]) -> str | None:
    """Chat input + botão Repetir/próxima (Bloco 5)."""
    pending = st.session_state.pending_chat_submit
    if pending and pending.get("prefix") == mode_prefix:
        st.session_state.pending_chat_submit = None
        return pending.get("text")

    last = _last_user_message(messages)
    if last and st.button(
        "↻ Repetir / próxima",
        key=f"{mode_prefix}_repeat",
        help="Reenvia a última mensagem do usuário (Streamlit não expõe seta ↑ no chat_input).",
    ):
        st.session_state.pending_chat_submit = {"prefix": mode_prefix, "text": last}
        st.rerun()

    placeholder = (
        "Ex.: Se eu bloquear com Wall of Omens, o que acontece?"
        if mode_prefix == "judge"
        else "Ex.: Quero um Niv-Mizzet, Parun Bracket 3, orçamento R$ 800"
    )
    return st.chat_input(placeholder, key=f"{mode_prefix}_chat_input")


def run_judge_mode(provider, settings) -> None:
    st.title("⚖️ Juiz Azorius")
    st.caption(
        "Juiz de Magic: The Gathering (Nível 3) com RAG sobre as Comprehensive Rules. "
        "Cite cartas pelo nome naturalmente — ou entre [[colchetes duplos]] para forçar. "
        "Passe o mouse sobre o nome da carta para o preview."
    )

    try:
        collection = get_collection(settings.llm_provider, settings.api_key)
    except (VectorDBError, ProviderError, ConfigError) as exc:
        st.error(str(exc))
        st.stop()

    if (
        st.session_state.pop("_hydrate_cards", False)
        and st.session_state.judge_messages
        and not st.session_state.card_history
    ):
        with st.spinner("Restaurando histórico de cartas da conversa..."):
            restored = hydrate_card_history_from_messages(
                provider, st.session_state.judge_messages
            )
            st.session_state.card_history = restored
            _persist_messages("judge", CONV_JUDGE, st.session_state.judge_messages)

    for i, message in enumerate(st.session_state.judge_messages):
        with st.chat_message(message["role"]):
            if message["role"] == "assistant":
                render_assistant_message(message["content"], message_key=f"j_{i}")
            else:
                _render_chat_markdown(message["content"], card_preview=False)

    question = _chat_controls("judge", st.session_state.judge_messages)

    if not question:
        render_cards_sidebar(st.session_state.card_history)
        return

    history = list(st.session_state.judge_messages)

    st.session_state.judge_messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        _render_chat_markdown(question, card_preview=False)

    card_names = extract_card_names(question)
    if not card_names:
        with st.spinner("Identificando cartas citadas..."):
            card_names = extract_card_names_llm(provider, question, history)

    cards: list[dict] = []
    missing_cards: list[str] = []
    with st.spinner("Consultando cartas no Scryfall..."):
        cards, missing_cards = resolve_cards(
            card_names, context=question, warn_missing=True, skip_known=False
        )

    try:
        with st.spinner("Buscando nas Comprehensive Rules..."):
            search_query = rewrite_rules_query(provider, question, history, cards)
            rules_context = query_rules(
                collection, provider.embed, search_query, n_results=8
            )
    except VectorDBError as exc:
        st.error(str(exc))
        update_card_history(cards)
        render_cards_sidebar(st.session_state.card_history)
        return

    with st.chat_message("assistant"):
        try:
            stream = generate_judge_ruling(
                provider, cards, rules_context, question, history, missing_cards
            )
            with st.spinner("O Juiz está deliberando... pode demorar um pouco"):
                first_chunk = next(stream, "")
            answer = st.write_stream(itertools.chain([first_chunk], stream))
        except ProviderError as exc:
            st.error(str(exc))
            update_card_history(cards)
            render_cards_sidebar(st.session_state.card_history)
            return

    st.session_state.judge_messages.append({"role": "assistant", "content": answer})
    _persist_messages("judge", CONV_JUDGE, st.session_state.judge_messages)

    with st.spinner("Atualizando histórico de cartas..."):
        answer_names = extract_card_names_from_answer(provider, answer)
        answer_cards, _ = resolve_cards(answer_names, context=answer)
        update_card_history(cards + answer_cards)
    render_cards_sidebar(st.session_state.card_history)


def _render_autopilot_panel(provider) -> None:
    st.subheader("Geração rápida (autopilot)")
    st.caption(
        "Motor Python: comandante → pool → identidade → budget LigaMagic (R$)/curva → lista. "
        "Sem micro-confirmações dos Passos 1–5. A primeira geração pode demorar (consulta de preços)."
    )
    with st.form("autopilot_form"):
        commander = st.text_input("Comandante", placeholder="Niv-Mizzet, Parun")
        bracket = st.select_slider("Bracket", options=[1, 2, 3, 4, 5], value=3)
        budget = st.number_input(
            "Orçamento máximo (R$ — LigaMagic)",
            min_value=0.0,
            value=800.0,
            step=50.0,
        )
        submitted = st.form_submit_button("Gerar deck", type="primary")

    if not submitted:
        return

    if not commander.strip():
        st.warning("Informe o comandante.")
        return

    clear_last_warning()
    with st.spinner(
        "Gerando deck (pool + preços LigaMagic em R$ — pode levar alguns minutos)..."
    ):
        result = build_autopilot_deck(commander.strip(), int(bracket), float(budget))

    if not result.get("ok"):
        st.error(result.get("error") or "Falha na geração.")
        opt = result.get("optimized") or {}
        if opt or result.get("priced_count") is not None:
            st.caption(
                f"Métricas: precificadas={result.get('priced_count', '?')} · "
                f"magias={opt.get('spell_count', '?')}/{opt.get('spell_slots', '?')} · "
                f"shortfall magias={opt.get('spell_shortfall', '?')} · "
                f"terrenos={opt.get('land_count', '?')}"
            )
        return

    warning = get_last_warning()
    if warning:
        st.warning(warning)
    if result.get("warning"):
        st.warning(result["warning"])

    export = result["export"]
    commander_name = result["commander"]["name"]
    total = result["optimized"].get("total_price", 0.0)
    st.caption(
        f"LigaMagic: R$ {float(result.get('total_brl', total)):.2f} · "
        f"precificadas={result.get('priced_count', 0)} "
        f"({result.get('priced_spells', 0)} magias) · "
        f"magias={result.get('spell_count', 0)} · "
        f"terrenos={result.get('land_count', 0)} · "
        f"shortfall magias={result.get('spell_shortfall', 0)} · "
        f"shortfall lands={result.get('land_shortfall', 0)}"
    )

    with st.spinner("Frase de apresentação..."):
        answer = narrate_autopilot_deck(
            provider,
            export,
            commander_name=commander_name,
            bracket=int(bracket),
            total_price_brl=float(total),
        )

    # Garante que a lista oficial exista na mensagem se o LLM falhar parcialmente.
    if "# " not in answer and export:
        answer = f"{answer.rstrip()}\n\n{export}"

    st.session_state.deckbuilder_messages.append(
        {
            "role": "user",
            "content": (
                f"[Geração rápida] {commander_name} · Bracket {bracket} · "
                f"budget R$ {budget:.0f}"
            ),
        }
    )
    st.session_state.deckbuilder_messages.append(
        {"role": "assistant", "content": answer}
    )
    _ingest_assistant_deck_text(answer)
    _persist_messages("deck", CONV_DECK, st.session_state.deckbuilder_messages)
    st.rerun()


def _render_upgrade_panel(provider) -> None:
    st.subheader("Melhorar lista")
    st.caption("Cole uma decklist (`Nx Nome`). O Python audita gaps; o LLM sugere upgrades.")
    with st.form("upgrade_form"):
        commander = st.text_input("Comandante (opcional)", key="upgrade_commander")
        bracket = st.select_slider(
            "Bracket alvo", options=[1, 2, 3, 4, 5], value=3, key="upgrade_bracket"
        )
        budget_note = st.text_input(
            "Orçamento (R$ — texto livre)",
            placeholder="R$ 800 / sem limite",
            key="upgrade_budget",
        )
        pasted = st.text_area(
            "Decklist",
            height=220,
            placeholder="# Comandante\n1x Sol Ring\n...",
            key="upgrade_paste",
        )
        submitted = st.form_submit_button("Auditar e pedir upgrades", type="primary")

    if not submitted:
        return
    if not pasted.strip():
        st.warning("Cole uma lista primeiro.")
        return

    clear_last_warning()
    with st.spinner(
        "Normalizando lista, preços LigaMagic (R$) e gaps (pode demorar)..."
    ):
        brief, analysis = build_upgrade_brief(
            pasted,
            commander_name=commander.strip() or None,
            bracket=int(bracket),
            budget_note=budget_note.strip(),
        )

    warning = get_last_warning()
    if warning:
        st.warning(warning)
    st.caption(
        f"Valor estimado LigaMagic: R$ {analysis.get('total_brl', 0):.2f}"
        + (
            f" · {len(analysis.get('unpriced') or [])} sem preço BRL"
            if analysis.get("unpriced")
            else ""
        )
    )

    user_msg = (
        f"Quero melhorar esta lista (Bracket {bracket}"
        + (f", {budget_note}" if budget_note.strip() else "")
        + ").\n\n"
        + pasted.strip()
    )
    history_before = list(st.session_state.deckbuilder_messages)
    st.session_state.deckbuilder_messages.append({"role": "user", "content": user_msg})
    # Lista colada já entra na sidebar.
    _ingest_assistant_deck_text(pasted)

    llm_input = (
        f"{brief}\n\n"
        "Com base na auditoria acima e na lista do jogador, inicie o Passo de upgrades "
        "pelo pacote com maior déficit. Liste cortes e entradas no formato Nx Nome. "
        "Orçamento e preços só em R$ (LigaMagic)."
    )
    with st.spinner("Consultando o Deckbuilder (upgrade)..."):
        try:
            answer = chat_deckbuilder_upgrade(provider, llm_input, history_before)
        except ProviderError as exc:
            st.error(str(exc))
            st.session_state.deckbuilder_messages.pop()
            return

    warning = get_last_warning()
    if warning:
        answer = f"_{warning}_\n\n{answer}"

    st.session_state.deckbuilder_messages.append(
        {"role": "assistant", "content": answer}
    )
    _ingest_assistant_deck_text(answer)
    _persist_messages("deck", CONV_DECK, st.session_state.deckbuilder_messages)

    if analysis.get("gaps"):
        st.info(
            "Gaps detectados: "
            + ", ".join(
                f"{g['package']} (−{g['deficit']})" for g in analysis["gaps"][:6]
            )
        )
    st.rerun()


def run_deckbuilder_mode(provider) -> None:
    st.title("🛠️ Deckbuilder Azorius")
    st.caption(
        "Oficina de Commander: chat em micro-passos, geração rápida (motor Python) "
        "ou melhoria de lista colada. A sidebar acumula a lista do deck. "
        "Orçamento em R$ via LigaMagic (mercado Brasil)."
    )

    # Conversas antigas / sessão sem lista: reconstrói a partir do chat.
    if (
        st.session_state.deckbuilder_messages
        and not st.session_state.assembled_deck
    ):
        rebuilt = rebuild_from_messages(st.session_state.deckbuilder_messages)
        if rebuilt:
            st.session_state.assembled_deck = rebuilt

    submode = st.radio(
        "Fluxo",
        options=["Criar do zero", "Melhorar lista", "Geração rápida"],
        horizontal=True,
        key="deck_submode",
    )

    if submode == "Geração rápida":
        _render_autopilot_panel(provider)
    elif submode == "Melhorar lista":
        _render_upgrade_panel(provider)

    for i, message in enumerate(st.session_state.deckbuilder_messages):
        with st.chat_message(message["role"]):
            if message["role"] == "assistant":
                render_assistant_message(message["content"], message_key=f"d_{i}")
            else:
                _render_chat_markdown(message["content"], card_preview=False)

    render_assembled_deck_sidebar(st.session_state.assembled_deck)

    # Chat agentic só nos fluxos de conversa (não no formulário de autopilot puro).
    if submode == "Geração rápida":
        return

    user_input = _chat_controls("deck", st.session_state.deckbuilder_messages)
    if not user_input:
        return

    history_before = list(st.session_state.deckbuilder_messages)
    st.session_state.deckbuilder_messages.append(
        {"role": "user", "content": user_input}
    )
    with st.chat_message("user"):
        _render_chat_markdown(user_input, card_preview=False)

    clear_last_warning()
    with st.chat_message("assistant"):
        try:
            with st.spinner("Montando o próximo pacote..."):
                if submode == "Melhorar lista":
                    answer = chat_deckbuilder_upgrade(
                        provider, user_input, history_before
                    )
                else:
                    answer = chat_deckbuilder(provider, user_input, history_before)
            warning = get_last_warning()
            if warning:
                st.caption(warning)
            render_assistant_message(
                answer, message_key=f"d_new_{len(st.session_state.deckbuilder_messages)}"
            )
        except ProviderError as exc:
            st.error(str(exc))
            return

    st.session_state.deckbuilder_messages.append(
        {"role": "assistant", "content": answer}
    )
    _ingest_assistant_deck_text(answer)
    _persist_messages("deck", CONV_DECK, st.session_state.deckbuilder_messages)
    st.rerun()


def main() -> None:
    _ensure_session_defaults()

    # Gate: motores só sobem depois de "Iniciar" / "Aplicar" nesta sessão.
    if not st.session_state.app_ready:
        render_setup_gate()
        return

    if not _session_settings_valid():
        st.session_state.app_ready = False
        st.rerun()

    mode = render_mode_sidebar()
    provider, settings = resolve_provider()

    if mode == MODE_DECKBUILDER:
        run_deckbuilder_mode(provider)
    else:
        run_judge_mode(provider, settings)


main()
