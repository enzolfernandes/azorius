"""Interface Streamlit do Juiz de MTG.

Este arquivo é EXCLUSIVAMENTE camada de apresentação: toda a lógica de negócio
vive em /services. Aqui apenas orquestramos as chamadas (injetando o provedor
de IA nos services que precisam dele) e traduzimos erros em mensagens de UI.
"""

import re

import streamlit as st

from services.config import ConfigError, load_settings
from services.llm_engine import generate_judge_ruling
from services.providers import ProviderError, get_provider
from services.scryfall_api import fetch_card_data
from services.vector_db import VectorDBError, initialize_db, query_rules

# Convenção da comunidade MTG: cartas citadas entre [[colchetes duplos]].
CARD_MENTION_PATTERN = re.compile(r"\[\[([^\[\]]+)\]\]")

st.set_page_config(page_title="Juiz Azorius — MTG", page_icon="⚖️", layout="wide")


# ---------------------------------------------------------------------------
# Bootstrap (cacheado): configuração, provedor de IA e banco vetorial.
# `st.cache_resource` garante uma única instância por processo — é a única
# concessão de estado nesta camada; os services permanecem puros.
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner="Inicializando o banco de regras (primeira execução demora)...")
def bootstrap():
    settings = load_settings()
    # Injeção de dependência: o provedor concreto (Gemini/OpenAI) é criado
    # aqui, uma única vez, e repassado aos services como parâmetro.
    provider = get_provider(settings)
    collection = initialize_db(embed_fn=provider.embed)
    return provider, collection


def extract_card_names(text: str) -> list[str]:
    """Extrai nomes de cartas citados como [[Nome da Carta]]."""
    seen: list[str] = []
    for name in CARD_MENTION_PATTERN.findall(text):
        cleaned = name.strip()
        if cleaned and cleaned.lower() not in (s.lower() for s in seen):
            seen.append(cleaned)
    return seen


def render_cards_sidebar(cards: list[dict]) -> None:
    """Exibe as imagens das cartas citadas na barra lateral."""
    with st.sidebar:
        st.header("Cartas citadas")
        if not cards:
            st.caption("Nenhuma carta citada. Use [[Nome da Carta]] na pergunta.")
            return
        for card in cards:
            if card["image_url"]:
                st.image(card["image_url"], caption=card["name"], use_container_width=True)
            else:
                st.write(f"**{card['name']}** (sem imagem disponível)")


def main() -> None:
    st.title("⚖️ Juiz Azorius")
    st.caption(
        "Juiz de Magic: The Gathering (Nível 3) com RAG sobre as Comprehensive Rules. "
        "Cite cartas entre [[colchetes duplos]]."
    )

    try:
        provider, collection = bootstrap()
    except ConfigError as exc:
        st.error(f"Configuração inválida: {exc}")
        st.stop()
    except (VectorDBError, ProviderError) as exc:
        st.error(str(exc))
        st.stop()

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "last_cards" not in st.session_state:
        st.session_state.last_cards = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    question = st.chat_input("Ex.: Se eu bloquear com [[Wall of Omens]], o que acontece?")

    if not question:
        render_cards_sidebar(st.session_state.last_cards)
        return

    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    # Passo A/B — cartas citadas na pergunta -> dados oficiais do Scryfall.
    card_names = extract_card_names(question)
    cards: list[dict] = []
    with st.spinner("Consultando cartas no Scryfall..."):
        for name in card_names:
            card = fetch_card_data(name)
            if card:
                cards.append(card)
            else:
                st.warning(f"Carta não encontrada no Scryfall: {name}")
    st.session_state.last_cards = cards
    render_cards_sidebar(cards)

    # Passo C — RAG: regras mais similares à pergunta.
    try:
        with st.spinner("Buscando nas Comprehensive Rules..."):
            rules_context = query_rules(collection, provider.embed, question)
    except VectorDBError as exc:
        st.error(str(exc))
        return

    # Passo D/E — injeção de contexto no LLM e streaming da resposta.
    with st.chat_message("assistant"):
        try:
            answer = st.write_stream(
                generate_judge_ruling(provider, cards, rules_context, question)
            )
        except ProviderError as exc:
            st.error(str(exc))
            return

    st.session_state.messages.append({"role": "assistant", "content": answer})


main()
