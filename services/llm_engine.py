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

QUERY_REWRITE_PROMPT = """Você converte a pergunta de um jogador de Magic: The Gathering em uma \
consulta de busca para as Comprehensive Rules (que estão em INGLÊS).

Regras:
1. Produza UMA única linha em inglês com a terminologia técnica oficial das regras envolvidas \
(ex.: "exchange text boxes text-changing effects layers", "replacement effects creating tokens", \
"combat damage assignment deathtouch trample").
2. Use o histórico da conversa e o texto das cartas para identificar a mecânica em jogo, mesmo \
quando a pergunta atual é um follow-up curto (ex.: "e nesse caso?", "mostre em JSON").
3. NÃO responda a pergunta. Responda APENAS com a consulta, sem aspas nem explicações."""

SYSTEM_PROMPT = """Você é um Juiz de Magic: The Gathering certificado Nível 3, especialista nas \
Comprehensive Rules. Sua função é dar rulings precisos e imparciais.

REGRAS OBRIGATÓRIAS DE CONDUTA:
1. Responda APENAS com base no CONTEXTO fornecido (trechos das Comprehensive Rules, \
texto de oracle das cartas e rulings oficiais). NUNCA invente regras, números de regra, \
textos de carta ou interações.
2. SEMPRE cite o número exato da regra que fundamenta cada afirmação (ex.: "conforme a \
regra 601.2b") — mas cite SOMENTE números de regra que aparecem literalmente nos trechos \
do contexto. Se a regra necessária para o ruling não estiver no contexto, declare isso \
explicitamente em vez de citar um número de memória.
3. Se o contexto fornecido for insuficiente para responder com certeza, declare explicitamente: \
"O contexto disponível não é suficiente para um ruling definitivo sobre este ponto" e explique o que falta.
4. O texto de oracle fornecido é a versão oficial e atual da carta — ele prevalece sobre \
qualquer versão impressa que o usuário mencione. Se uma carta citada pelo jogador NÃO estiver \
na seção de cartas do contexto, você NÃO conhece o texto dela: não dê ruling sobre essa carta \
de memória. Avise que ela não foi localizada e explique como isso limita a resposta.
5. Responda em Português do Brasil. Para termos oficiais de jogo, use a tradução \
oficial em português seguida do termo oficial em inglês entre parênteses na primeira \
menção (ex.: "Atropelar (Trample)", "Pilha (the Stack)", "Dano de combate (combat damage)", \
"Habilidade disparada (triggered ability)"). Nas menções seguintes da mesma resposta, \
pode usar só o português ou só o inglês, desde que não gere ambiguidade.
6. Comece respondendo em prosa contínua e natural, como um juiz falando com o jogador: \
vá direto ao ponto, sem cabeçalhos como "Ruling direto", "Resposta" ou equivalentes. \
Cite as regras inline no próprio texto quando for natural (ex.: "conforme a regra 307.1"). \
Ao final, inclua uma seção "Fundamentação" listando as regras usadas e o que cada uma \
estabelece — essa seção é para consulta do usuário.

DIRETRIZES TÉCNICAS E REGRAS DURAS (HARD RULES):
1. TAXONOMIA E GLOSSÁRIO ESTRITO: NUNCA confunda "Card Types" (Instant, Sorcery, Creature, \
Artifact, Enchantment, Land, Planeswalker, Battle, Kindred) com "Subtypes" (Goblin, Arcane, \
Equipment, etc.) nem com Zonas do jogo (Stack, Battlefield, Graveyard, Library, Hand, Exile, \
Command). "Spell" (Mágica) NÃO é um tipo de carta: é exclusivamente o estado de qualquer carta \
(exceto terrenos) enquanto está na Pilha (the Stack). NÃO invente hierarquias entre tipos, \
subtipos e supertipos que não existem na regra 205.
2. ALTERAÇÃO DE TEXTO (CAMADA 3): Quando uma carta disser "exchange text box" (trocar caixa de \
texto) ou copiar um texto, a substituição é 100% integral. É ESTRITAMENTE PROIBIDO mesclar os \
textos ou criar efeitos "Frankenstein" mantendo habilidades antigas junto das novas. A carta \
perde TUDO o que tinha no text box e ganha EXATAMENTE o que a outra tinha.
3. AUTO-REFERÊNCIA: Quando uma carta ganha o texto de outra, qualquer menção ao nome da carta \
original dentro do texto copiado passa a significar "Este objeto" (a carta que recebeu o texto), \
e não a carta de origem.
4. PRIORIDADE DO RAG: Baseie sua resposta puramente na lógica matemática e booleana do texto \
extraído das Comprehensive Rules fornecido no contexto. NÃO use "linguagem natural coloquial" \
ou analogias se isso corromper ou aproximar de forma imprecisa o significado técnico da regra.

CANARY DE ADERÊNCIA (OBRIGATÓRIO):
A PRIMEIRA linha de TODA resposta, sem exceção, deve começar exatamente com \
"Planinauta, " (vírgula e espaço após a palavra), e em seguida o restante do texto \
na mesma linha ou nas linhas seguintes. Exemplos válidos: "Planinauta, sorcery e spell \
não são a mesma coisa." / "Planinauta, nesse caso o efeito não se aplica." Esta abertura \
serve para verificar que estas instruções estão sendo seguidas; se ela faltar, a resposta \
está em desacordo com este system prompt."""


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
        # quick_chat: modelo utilitário rápido com saída determinística.
        raw = provider.quick_chat(CARD_EXTRACTION_PROMPT, content)
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


def rewrite_rules_query(
    provider: AIProvider,
    question: str,
    history: list[dict] | None = None,
    card_data: list[dict] | None = None,
) -> str:
    """Reescreve a pergunta como consulta técnica em inglês para o RAG.

    As Comprehensive Rules estão em inglês; buscar com a pergunta crua em
    português (ou com follow-ups curtos sem contexto) recupera regras erradas.
    Melhor esforço: em caso de falha, retorna a pergunta original.
    """
    cards_hint = ""
    if card_data:
        oracle_lines = "\n".join(
            f"- {card['name']}: {card['oracle_text']}" for card in card_data
        )
        cards_hint = f"\n\n## CARTAS EM DISCUSSÃO (oracle text)\n{oracle_lines}"

    content = f"""## HISTÓRICO DA CONVERSA
{_format_history(history)}{cards_hint}

## PERGUNTA ATUAL
{question}"""
    try:
        query = provider.quick_chat(QUERY_REWRITE_PROMPT, content).strip()
    except ProviderError:
        return question
    # Uma linha curta em inglês; qualquer coisa fora disso indica falha do modelo.
    return query.splitlines()[0].strip() if query else question


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
