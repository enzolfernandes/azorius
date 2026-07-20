"""Interface Streamlit do Juiz / Deckbuilder de MTG.

Este arquivo é EXCLUSIVAMENTE camada de apresentação: toda a lógica de negócio
vive em /services. Aqui apenas orquestramos as chamadas (injetando o provedor
de IA nos services que precisam dele) e traduzimos erros em mensagens de UI.
"""

import itertools
import re

import streamlit as st

from services.config import (
    CHROMA_DIR,
    VALID_PROVIDERS,
    ConfigError,
    api_key_from_env,
    env_defaults,
    settings_from_values,
)
from services.llm_engine import (
    DECKBUILDER_BOOTSTRAP_MESSAGE,
    chat_deckbuilder,
    extract_card_names_from_answer,
    extract_card_names_llm,
    generate_judge_ruling,
    rewrite_rules_query,
)
from services.providers import ProviderError, get_provider
from services.scryfall_api import fetch_card_data
from services.vector_db import VectorDBError, initialize_db, query_rules

# Convenção da comunidade MTG: cartas citadas entre [[colchetes duplos]].
CARD_MENTION_PATTERN = re.compile(r"\[\[([^\[\]]+)\]\]")

MODE_JUDGE = "Modo Juiz"
MODE_DECKBUILDER = "Modo Deckbuilder"

st.set_page_config(page_title="Azorius — MTG", page_icon="⚖️", layout="wide")


# ---------------------------------------------------------------------------
# Banco vetorial cacheado por provedor (Gemini ≠ OpenAI embeddings).
# `_api_key` não entra na chave do cache: trocar a chave no mesmo provedor
# não reingesta; a primeira ingestão usa a chave de quem disparou o cache.
# ---------------------------------------------------------------------------
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


def _bootstrap_deckbuilder_chat() -> None:
    """Limpa o chat do Deckbuilder e injeta o gatilho inicial (sem API)."""
    st.session_state.deckbuilder_messages = [
        {"role": "assistant", "content": DECKBUILDER_BOOTSTRAP_MESSAGE}
    ]


def render_mode_and_provider_sidebar() -> str:
    """Modo da app + provedor/chave. Detecta troca para o Deckbuilder."""
    if "app_mode" not in st.session_state:
        st.session_state.app_mode = MODE_JUDGE
    if "_prev_app_mode" not in st.session_state:
        st.session_state._prev_app_mode = st.session_state.app_mode
    if "llm_provider" not in st.session_state or "api_key" not in st.session_state:
        default_provider, default_key = env_defaults()
        st.session_state.llm_provider = default_provider
        st.session_state.api_key = default_key
    if "judge_messages" not in st.session_state:
        st.session_state.judge_messages = []
    if "deckbuilder_messages" not in st.session_state:
        st.session_state.deckbuilder_messages = []
    if "card_history" not in st.session_state:
        st.session_state.card_history = []

    with st.sidebar:
        st.header("Modo")
        st.radio(
            "Escolha o modo",
            options=[MODE_JUDGE, MODE_DECKBUILDER],
            key="app_mode",
            label_visibility="collapsed",
        )

        st.header("Configuração")
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
                st.session_state[widget_key] = api_key_from_env(provider)

        api_key = st.text_input(
            key_label,
            type="password",
            key=widget_key,
            help="A chave fica só nesta sessão do navegador; não é gravada em disco.",
        )
        if st.button("Aplicar", type="primary", use_container_width=True):
            st.session_state.llm_provider = provider
            st.session_state.api_key = api_key.strip()
            st.rerun()

        st.caption(
            "Opcional: preencha um `.env` local para uso solo. "
            "Em demo compartilhada, cada pessoa usa a própria chave."
        )

    current = st.session_state.app_mode
    previous = st.session_state._prev_app_mode
    if current == MODE_DECKBUILDER and previous != MODE_DECKBUILDER:
        _bootstrap_deckbuilder_chat()
    elif current == MODE_DECKBUILDER and not st.session_state.deckbuilder_messages:
        _bootstrap_deckbuilder_chat()
    st.session_state._prev_app_mode = current
    return current


def render_cards_sidebar(cards: list[dict]) -> None:
    """Exibe o histórico de cartas da conversa na barra lateral (recentes no topo)."""
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


def update_card_history(new_cards: list[dict]) -> None:
    """Insere o lote no topo do histórico, preservando a ordem do lote."""
    if not new_cards:
        return
    ordered: list[dict] = []
    seen: set[str] = set()
    for card in new_cards:
        key = card["name"].lower()
        if key in seen:
            continue
        ordered.append(card)
        seen.add(key)
    history = [
        card
        for card in st.session_state.card_history
        if card["name"].lower() not in seen
    ]
    st.session_state.card_history = ordered + history


def resolve_cards(
    names: list[str],
    *,
    context: str,
    warn_missing: bool = False,
    skip_known: bool = True,
) -> tuple[list[dict], list[str]]:
    """Resolve nomes no Scryfall."""
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
    """Valida settings da sessão e devolve o AIProvider (ou interrompe a UI)."""
    try:
        settings = settings_from_values(
            st.session_state.llm_provider, st.session_state.api_key
        )
    except ConfigError as exc:
        st.info(
            "Configure o **provedor** e a **chave de API** na barra lateral e clique em "
            "**Aplicar**. Você também pode usar um arquivo `.env` local "
            "(ver README)."
        )
        st.warning(str(exc))
        st.stop()

    try:
        return get_provider(settings), settings
    except (ProviderError, ConfigError) as exc:
        st.error(str(exc))
        st.stop()


def run_judge_mode(provider, settings) -> None:
    """Fluxo RAG do Juiz."""
    st.title("⚖️ Juiz Azorius")
    st.caption(
        "Juiz de Magic: The Gathering (Nível 3) com RAG sobre as Comprehensive Rules. "
        "Cite cartas pelo nome naturalmente — ou entre [[colchetes duplos]] para forçar."
    )

    try:
        collection = get_collection(settings.llm_provider, settings.api_key)
    except (VectorDBError, ProviderError, ConfigError) as exc:
        st.error(str(exc))
        st.stop()

    for message in st.session_state.judge_messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    question = st.chat_input("Ex.: Se eu bloquear com Wall of Omens, o que acontece?")

    if not question:
        render_cards_sidebar(st.session_state.card_history)
        return

    history = list(st.session_state.judge_messages)

    st.session_state.judge_messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

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

    with st.spinner("Atualizando histórico de cartas..."):
        answer_names = extract_card_names_from_answer(provider, answer)
        answer_cards, _ = resolve_cards(answer_names, context=answer)
        update_card_history(cards + answer_cards)
    render_cards_sidebar(st.session_state.card_history)


def run_deckbuilder_mode(provider) -> None:
    """Chat agentic do Deckbuilder (micro-passos 0–5 + tools de preço)."""
    st.title("🛠️ Deckbuilder Azorius")
    st.caption(
        "Oficina de Commander por pacotes: briefing, wincons, motor, sinergia, "
        "interação e mana — um passo por vez. Preços via Scryfall quando houver budget."
    )

    for message in st.session_state.deckbuilder_messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    user_input = st.chat_input(
        "Ex.: Quero um Niv-Mizzet, Parun Bracket 3, orçamento US$ 150"
    )
    if not user_input:
        return

    history_before = list(st.session_state.deckbuilder_messages)
    st.session_state.deckbuilder_messages.append(
        {"role": "user", "content": user_input}
    )
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        try:
            with st.spinner("Montando o próximo pacote..."):
                answer = chat_deckbuilder(provider, user_input, history_before)
            st.markdown(answer)
        except ProviderError as exc:
            st.error(str(exc))
            return

    st.session_state.deckbuilder_messages.append(
        {"role": "assistant", "content": answer}
    )


def main() -> None:
    mode = render_mode_and_provider_sidebar()
    provider, settings = resolve_provider()

    if mode == MODE_DECKBUILDER:
        run_deckbuilder_mode(provider)
    else:
        run_judge_mode(provider, settings)


main()
