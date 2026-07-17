"""Interface Streamlit do Juiz de MTG.

Este arquivo é EXCLUSIVAMENTE camada de apresentação: toda a lógica de negócio
vive em /services. Aqui apenas orquestramos as chamadas (injetando o provedor
de IA nos services que precisam dele) e traduzimos erros em mensagens de UI.
"""

import itertools
import re

import streamlit as st

from services.config import ConfigError, load_settings
from services.llm_engine import (
    extract_card_names_llm,
    generate_judge_ruling,
    rewrite_rules_query,
)
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
    """Exibe o histórico de cartas da conversa na barra lateral (recentes no topo)."""
    with st.sidebar:
        st.header("Histórico de cartas")
        if not cards:
            st.caption("Nenhuma carta citada ainda. Basta mencionar o nome na pergunta.")
            return
        for card in cards:
            if card["image_url"]:
                st.image(card["image_url"], caption=card["name"], width="stretch")
            else:
                st.write(f"**{card['name']}** (sem imagem disponível)")


def update_card_history(new_cards: list[dict]) -> None:
    """Acumula cartas no histórico da sessão, sem duplicatas, recentes primeiro."""
    known = {card["name"].lower() for card in st.session_state.card_history}
    fresh = [card for card in new_cards if card["name"].lower() not in known]
    st.session_state.card_history = fresh + st.session_state.card_history


def main() -> None:
    st.title("⚖️ Juiz Azorius")
    st.caption(
        "Juiz de Magic: The Gathering (Nível 3) com RAG sobre as Comprehensive Rules. "
        "Cite cartas pelo nome naturalmente — ou entre [[colchetes duplos]] para forçar."
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
    if "card_history" not in st.session_state:
        st.session_state.card_history = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    question = st.chat_input("Ex.: Se eu bloquear com Wall of Omens, o que acontece?")

    if not question:
        render_cards_sidebar(st.session_state.card_history)
        return

    # Histórico ANTES da pergunta atual: dá memória à extração e ao ruling.
    history = list(st.session_state.messages)

    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    # Passo A/B — cartas citadas na pergunta -> dados oficiais do Scryfall.
    # Extração híbrida: [[colchetes]] são determinísticos e têm prioridade;
    # sem eles, o LLM identifica nomes em escrita natural (inclusive PT->EN),
    # usando o histórico para resolver referências a cartas já discutidas.
    card_names = extract_card_names(question)
    if not card_names:
        with st.spinner("Identificando cartas citadas..."):
            card_names = extract_card_names_llm(provider, question, history)

    cards: list[dict] = []
    missing_cards: list[str] = []
    with st.spinner("Consultando cartas no Scryfall..."):
        for name in card_names:
            card = fetch_card_data(name)
            if card:
                cards.append(card)
            else:
                missing_cards.append(name)
                st.warning(f"Carta não encontrada no Scryfall: {name}")
    update_card_history(cards)
    render_cards_sidebar(st.session_state.card_history)

    # Passo C — RAG: reescreve a pergunta como consulta técnica em inglês
    # (idioma das regras), usando histórico e oracle text para dar contexto a
    # follow-ups curtos; depois busca as regras mais similares.
    try:
        with st.spinner("Buscando nas Comprehensive Rules..."):
            search_query = rewrite_rules_query(provider, question, history, cards)
            rules_context = query_rules(
                collection, provider.embed, search_query, n_results=8
            )
    except VectorDBError as exc:
        st.error(str(exc))
        return

    # Passo D/E — injeção de contexto no LLM e streaming da resposta.
    with st.chat_message("assistant"):
        try:
            stream = generate_judge_ruling(
                provider, cards, rules_context, question, history, missing_cards
            )
            # O modelo de raciocínio fica em silêncio (às vezes por mais de um
            # minuto) antes do primeiro token; o spinner cobre essa espera e
            # some assim que o streaming de fato começa.
            with st.spinner("O Juiz está deliberando... pode demorar um pouco"):
                first_chunk = next(stream, "")
            answer = st.write_stream(itertools.chain([first_chunk], stream))
        except ProviderError as exc:
            st.error(str(exc))
            return

    st.session_state.messages.append({"role": "assistant", "content": answer})


main()
