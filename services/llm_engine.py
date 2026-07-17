"""Montagem do prompt de juiz e geração da resposta via LLM.

Módulo puro: sem Streamlit. O provedor de LLM é injetado como parâmetro
(`AIProvider`), e a resposta é devolvida como um generator de strings — a
camada de UI decide como consumir o streaming (st.write_stream hoje,
StreamingResponse no FastAPI amanhã), sem que este módulo saiba quem o chama.
"""

import json
import re
from collections.abc import Iterator

from .providers import AIProvider, ProviderError

# Limita as consultas subsequentes ao Scryfall em perguntas muito longas.
MAX_EXTRACTED_CARDS = 6

# Mensagens recentes injetadas nos prompts para dar memória à conversa sem
# estourar o orçamento de tokens.
MAX_HISTORY_MESSAGES = 10

CARD_EXTRACTION_PROMPT = """Você identifica cartas de Magic: The Gathering citadas na PERGUNTA ATUAL \
de uma conversa entre um jogador e um juiz.

Regras:
1. Extraia TODOS os nomes que se referem claramente a cartas específicas: mencionados de forma \
explícita na pergunta atual, ou referenciados nela por pronome/apelido a uma carta que aparece \
no histórico da conversa. Não deixe nenhuma carta mencionada de fora.
2. Se tiver CERTEZA do nome oficial em inglês, retorne-o (apelidos: "bolt" -> "Lightning Bolt"). \
Se NÃO tiver certeza da tradução, retorne o nome EXATAMENTE como o jogador escreveu, no idioma \
original, corrigindo apenas erros de digitação ("Procissão dos Unigdos" -> "Procissão dos Ungidos"). \
NUNCA invente uma tradução aproximada — o sistema resolve nomes oficiais em qualquer idioma.
3. NÃO inclua termos genéricos do jogo (criatura, terreno, mágica, token, comandante, instantânea) \
nem tipos ou palavras comuns que coincidem com nomes de cartas, a menos que o texto trate \
claramente da carta específica.
4. NÃO deduza cartas que não foram mencionadas.
5. Responda APENAS com JSON válido, sem texto adicional: {"cards": ["Nome 1"]} ou {"cards": []}

Exemplos:
- Pergunta: "Posso dar bolt no comandante dele?" -> {"cards": ["Lightning Bolt"]}
- Pergunta: "Com Vidas Paralelas e Temporada da Multiplicação em campo, quantos tokens crio?" -> {"cards": ["Parallel Lives", "Temporada da Multiplicação"]}
- Pergunta: "E se a minha muralha tiver deathtouch?" com histórico citando Wall of Omens -> {"cards": ["Wall of Omens"]}
- Pergunta: "Quantos terrenos posso jogar por turno?" -> {"cards": []}
- Pergunta: "Minha criatura com trample ataca e ele bloqueia com um token" -> {"cards": []}"""

SYSTEM_PROMPT = """Você é um Juiz de Magic: The Gathering certificado Nível 3, especialista nas \
Comprehensive Rules. Sua função é dar rulings precisos e imparciais.

REGRAS OBRIGATÓRIAS DE CONDUTA:
1. Responda APENAS com base no CONTEXTO fornecido (trechos das Comprehensive Rules, \
texto de oracle das cartas e rulings oficiais). NUNCA invente regras, números de regra, \
textos de carta ou interações.
2. SEMPRE cite o número exato da regra que fundamenta cada afirmação (ex.: "conforme a regra 601.2b").
3. Se o contexto fornecido for insuficiente para responder com certeza, declare explicitamente: \
"O contexto disponível não é suficiente para um ruling definitivo sobre este ponto" e explique o que falta.
4. O texto de oracle fornecido é a versão oficial e atual da carta — ele prevalece sobre \
qualquer versão impressa que o usuário mencione. Se uma carta citada pelo jogador NÃO estiver \
na seção de cartas do contexto, você NÃO conhece o texto dela: não dê ruling sobre essa carta \
de memória. Avise que ela não foi localizada e explique como isso limita a resposta.
5. Responda em Português do Brasil, mas mantenha termos de jogo consagrados em inglês \
(stack, trigger, oracle text, etc.) quando a tradução gerar ambiguidade.
6. Estruture a resposta: ruling direto primeiro, fundamentação com citações das regras depois."""


def _format_history(history: list[dict] | None) -> str:
    """Serializa as últimas mensagens da conversa (role/content) para prompt."""
    if not history:
        return "(conversa recém-iniciada, sem mensagens anteriores)"
    recent = history[-MAX_HISTORY_MESSAGES:]
    labels = {"user": "Jogador", "assistant": "Juiz"}
    return "\n\n".join(
        f"{labels.get(msg['role'], msg['role'])}: {msg['content']}" for msg in recent
    )


def extract_card_names_llm(
    provider: AIProvider, question: str, history: list[dict] | None = None
) -> list[str]:
    """Identifica cartas citadas em escrita natural, via LLM (melhor esforço).

    Retorna nomes oficiais em inglês, prontos para a busca fuzzy do Scryfall.
    O histórico permite resolver referências como "a minha muralha" a cartas
    citadas em turnos anteriores. Falhas de API ou JSON malformado retornam
    lista vazia — a extração é auxiliar e não deve derrubar o fluxo; um
    problema real de API vai aparecer na geração do ruling logo em seguida.
    """
    content = f"""## HISTÓRICO DA CONVERSA
{_format_history(history)}

## PERGUNTA ATUAL
{question}"""
    try:
        # Temperatura 0: extração estruturada precisa ser determinística.
        raw = "".join(provider.stream_chat(CARD_EXTRACTION_PROMPT, content, temperature=0.0))
    except ProviderError:
        return []

    # O modelo pode envolver o JSON em texto ou cercas de código.
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return []
    try:
        names = json.loads(match.group(0)).get("cards", [])
    except (json.JSONDecodeError, AttributeError):
        return []

    seen: list[str] = []
    for name in names:
        if isinstance(name, str) and name.strip():
            cleaned = name.strip()
            if cleaned.lower() not in (s.lower() for s in seen):
                seen.append(cleaned)
    return seen[:MAX_EXTRACTED_CARDS]


def _format_cards_section(cards: list[dict]) -> str:
    """Serializa os dados das cartas (oracle text + rulings) para o prompt."""
    if not cards:
        return "Nenhuma carta específica foi citada na pergunta."

    blocks = []
    for card in cards:
        lines = [
            f"### {card['name']} — {card['type_line']} {card['mana_cost']}".strip(),
            f"Oracle text:\n{card['oracle_text'] or '(sem texto de regras)'}",
        ]
        if card["rulings"]:
            rulings = "\n".join(f"- {ruling}" for ruling in card["rulings"])
            lines.append(f"Rulings oficiais:\n{rulings}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _format_rules_section(rules_context: list[dict]) -> str:
    """Serializa os trechos recuperados das Comprehensive Rules para o prompt."""
    if not rules_context:
        return "Nenhuma regra relevante foi recuperada."
    return "\n\n".join(
        f"[Regra {rule['rule_number']}]\n{rule['text']}" for rule in rules_context
    )


def build_user_prompt(
    card_data: list[dict],
    rules_context: list[dict],
    user_question: str,
    history: list[dict] | None = None,
    missing_cards: list[str] | None = None,
) -> str:
    """Monta o prompt de contexto injetando histórico, regras, cartas e a pergunta."""
    missing_section = ""
    if missing_cards:
        names = ", ".join(missing_cards)
        missing_section = f"""

## ATENÇÃO — CARTAS CITADAS MAS NÃO LOCALIZADAS
O jogador mencionou estas cartas, mas os dados oficiais NÃO foram encontrados: {names}.
Você NÃO conhece o texto delas. Não dê ruling sobre essas cartas de memória: avise o jogador \
que elas não foram localizadas e explique como isso limita a resposta."""

    return f"""## HISTÓRICO DA CONVERSA (para dar continuidade a rulings anteriores)
{_format_history(history)}

## CONTEXTO — COMPREHENSIVE RULES (trechos oficiais recuperados)
{_format_rules_section(rules_context)}

## CONTEXTO — CARTAS CITADAS (dados oficiais do Scryfall)
{_format_cards_section(card_data)}{missing_section}

## PERGUNTA ATUAL DO JOGADOR
{user_question}

Dê seu ruling seguindo estritamente as regras de conduta. Se a pergunta fizer referência \
a algo discutido antes (uma carta, um cenário), use o histórico para entender o contexto."""


def generate_judge_ruling(
    provider: AIProvider,
    card_data: list[dict],
    rules_context: list[dict],
    user_question: str,
    history: list[dict] | None = None,
    missing_cards: list[str] | None = None,
) -> Iterator[str]:
    """Gera o ruling do juiz em streaming (generator de pedaços de texto).

    O `provider` é injetado pelo chamador — este módulo não sabe se é Gemini
    ou OpenAI, apenas usa a interface comum `stream_chat`. O `history` são as
    mensagens anteriores do chat ({"role", "content"}); `missing_cards` são
    cartas citadas cujos dados não foram encontrados — o juiz é instruído a
    não opinar sobre elas de memória.
    """
    user_prompt = build_user_prompt(
        card_data, rules_context, user_question, history, missing_cards
    )
    yield from provider.stream_chat(SYSTEM_PROMPT, user_prompt)
