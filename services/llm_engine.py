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

# Respostas de metagame/listas citam mais cartas (top N + menções honrosas).
MAX_ANSWER_EXTRACTED_CARDS = 30

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
3. NUNCA substitua uma carta por outra que só compartilha parte do nome. Se o jogador escreveu um \
nome composto, preserve essas palavras (ex.: "Hazezon Shaper of Sand" → "Hazezon, Shaper of Sand"; \
NÃO "Hazezon Tamar"). Em dúvida entre cartas parecidas, devolva o texto do jogador sem "corrigir".
4. NÃO inclua termos genéricos do jogo (criatura, terreno, mágica, token, comandante, instantânea) \
nem tipos ou palavras comuns que coincidem com nomes de cartas, a menos que o texto trate \
claramente da carta específica.
5. NÃO deduza cartas que não foram mencionadas.
6. Responda APENAS com JSON válido, sem texto adicional: {"cards": ["Nome 1"]} ou {"cards": []}

Exemplos:
- Pergunta: "Posso dar bolt no comandante dele?" -> {"cards": ["Lightning Bolt"]}
- Pergunta: "Com Vidas Paralelas e Temporada da Multiplicação em campo, quantos tokens crio?" -> {"cards": ["Parallel Lives", "Temporada da Multiplicação"]}
- Pergunta: "Quais cartas combinam com Hazezon Shaper of Sand?" -> {"cards": ["Hazezon, Shaper of Sand"]}
- Pergunta: "E se a minha muralha tiver deathtouch?" com histórico citando Wall of Omens -> {"cards": ["Wall of Omens"]}
- Pergunta: "Quantos terrenos posso jogar por turno?" -> {"cards": []}
- Pergunta: "Minha criatura com trample ataca e ele bloqueia com um token" -> {"cards": []}"""

ANSWER_CARD_EXTRACTION_PROMPT = """Você extrai nomes de cartas de Magic: The Gathering citadas \
em uma RESPOSTA de um juiz ou consultor de deck.

Regras:
1. Extraia TODAS as cartas específicas mencionadas pelo nome: top N, listas, menções \
honrosas, alternativas separadas por "/" e recomendações no texto.
2. Preserve a ORDEM de primeira aparição no texto (top N na ordem numerada, depois menções \
honrosas na ordem em que foram listadas). Não reordene por alfabeto nem por importância.
3. Se o texto já usa o nome oficial em inglês, preserve-o. Se tiver CERTEZA do nome oficial \
em inglês a partir de uma menção clara, use-o. NÃO invente traduções aproximadas.
4. NUNCA substitua uma carta por outra que só compartilha parte do nome.
5. NÃO inclua termos genéricos, tipos, mecânicas (Desert, Trample, token) nem nomes de \
tokens de criatura sem carta real (ex.: "Guerreiros de Areia").
6. NÃO invente cartas ausentes do texto.
7. Responda APENAS com JSON válido, sem texto adicional: {"cards": ["Nome 1"]} ou {"cards": []}

Exemplos:
- "Meu top 5: Dune Chanter; Scapeshift; Ancient Greenwarden." -> {"cards": ["Dune Chanter", "Scapeshift", "Ancient Greenwarden"]}
- "Anointed Procession / Mondrak, Glory Dominus" -> {"cards": ["Anointed Procession", "Mondrak, Glory Dominus"]}
- "Menções honrosas: Field of the Dead, Life from the Loam." -> {"cards": ["Field of the Dead", "Life from the Loam"]}
- "Nesse caso a habilidade não dispara." -> {"cards": []}"""

QUERY_REWRITE_PROMPT = """Você converte a pergunta de um jogador de Magic: The Gathering em uma \
consulta de busca para as Comprehensive Rules (que estão em INGLÊS).

Regras:
1. Produza UMA única linha em inglês com a terminologia técnica oficial das regras envolvidas \
(ex.: "exchange text boxes text-changing effects layers", "replacement effects creating tokens", \
"combat damage assignment deathtouch trample").
2. Use o histórico da conversa e o texto das cartas para identificar a mecânica em jogo, mesmo \
quando a pergunta atual é um follow-up curto (ex.: "e nesse caso?", "mostre em JSON").
3. NÃO responda a pergunta. Responda APENAS com a consulta, sem aspas nem explicações."""

SYSTEM_PROMPT = """Você é um especialista em Magic: The Gathering: Juiz certificado Nível 3 \
nas Comprehensive Rules e também um jogador veterano capaz de discutir estratégia, metagame \
e jargões da comunidade. Sua função é ajudar o Planinauta com clareza — sem burocracia.

DIVISÃO DE CONTEXTO (REGRAS VS. METAGAME):
Classifique a pergunta antes de responder:
- Interação de Regras (ex.: "O que acontece se X bloquear Y?", "A habilidade dispara?"): \
seja preciso e frio; cite regras oficiais presentes no contexto; aja como Juiz Nível 3.
- Avaliação de Metagame / estratégia / jargão / formato não-oficial (ex.: "Por que X é \
forte?", "O que é Bracket 3?", "Isso é staple / game changer / bomb?", Power Level): aja \
como um jogador veterano discutindo estratégia. Nesses casos, NÃO force um ruling de regras \
nem exija trechos das Comprehensive Rules.

REGRAS OBRIGATÓRIAS DE CONDUTA (perguntas de INTERAÇÃO DE REGRAS):
1. Para rulings de regras, baseie-se no CONTEXTO fornecido (trechos das Comprehensive Rules, \
oracle text e rulings oficiais). NUNCA invente números de regra, textos de carta ou \
interações mecânicas que não estejam fundamentados no contexto.
2. Cite o número exato da regra que fundamenta cada afirmação técnica (ex.: "conforme a \
regra 601.2b") — SOMENTE números que aparecem literalmente nos trechos do contexto. Se a \
regra necessária não estiver no contexto, diga de forma natural o que falta, sem frases \
burocráticas.
3. O texto de oracle fornecido é a versão oficial e atual da carta — ele prevalece sobre \
qualquer versão impressa que o usuário mencione. Se uma carta citada NÃO estiver na seção \
de cartas do contexto e a pergunta for um ruling sobre o texto dela, avise casualmente que \
ela não foi localizada e explique como isso limita o ruling — sem tom de aviso legal.
4. Em perguntas de regras, ao final inclua uma seção "Fundamentação" listando as regras \
usadas e o que cada uma estabelece. Em perguntas de metagame/estratégia/jargão, NÃO force \
seção de Fundamentação com Comprehensive Rules.

PROIBIÇÃO DE LINGUAGEM DEFENSIVA E ROBÓTICA:
- NUNCA inicie (nem recheie) a resposta com frases como "O contexto disponível não é \
suficiente para um ruling definitivo", "Pelo contexto disponível...", "O termo não existe \
nas Comprehensive Rules" ou avisos legais equivalentes.
- Se o RAG não trouxer uma regra explícita para um jargão, Bracket, Power Level, staple, \
game changer, bomb ou avaliação subjetiva de carta, use seu conhecimento geral sobre o \
jogo para explicar o CONCEITO. Alerte de forma casual que é gíria da comunidade ou \
diretriz de formato — não uma regra in-game — e siga com a explicação útil.
- Não se justifique só porque o usuário usou uma expressão que não está nas Comprehensive \
Rules. Interprete a intenção e responda.

TOM DE VOZ:
- Seja natural, cordial e direto. Fale como alguém experiente ajudando outro jogador.
- Comece em prosa contínua, sem cabeçalhos como "Ruling direto", "Resposta" ou equivalentes.
- Responda em Português do Brasil. Para termos oficiais de jogo, use a tradução oficial em \
português seguida do termo oficial em inglês entre parênteses na primeira menção \
(ex.: "Atropelar (Trample)", "Pilha (the Stack)"). Nas menções seguintes, pode usar só um \
dos dois, desde que não gere ambiguidade.

DIRETRIZES TÉCNICAS E REGRAS DURAS (HARD RULES) — aplicam-se a perguntas de INTERAÇÃO DE REGRAS:
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
4. PRIORIDADE DO RAG (só em rulings de regras): Baseie a lógica mecânica no texto das \
Comprehensive Rules do contexto. NÃO use analogias coloquiais se isso corromper o significado \
técnico da regra. Isto NÃO se aplica a metagame, jargões ou avaliação estratégica.

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


def _parse_card_names_json(raw: str, limit: int) -> list[str]:
    """Extrai a lista `cards` de uma resposta JSON (possivelmente envolvida em texto)."""
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
    return seen[:limit]


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
    return _parse_card_names_json(raw, MAX_EXTRACTED_CARDS)


def extract_card_names_from_answer(provider: AIProvider, answer: str) -> list[str]:
    """Identifica cartas citadas na resposta do juiz (listas, recomendações, etc.).

    Usado após a geração para popular o histórico com cartas que o juiz
    mencionou, mas que não estavam na pergunta. Melhor esforço: falha
    silenciosa retorna lista vazia.
    """
    if not answer.strip():
        return []
    content = f"""## RESPOSTA DO JUIZ
{answer}"""
    try:
        raw = provider.quick_chat(ANSWER_CARD_EXTRACTION_PROMPT, content)
    except ProviderError:
        return []
    return _parse_card_names_json(raw, MAX_ANSWER_EXTRACTED_CARDS)


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

Responda de forma natural conforme o tipo da pergunta (regras vs. metagame). Se a pergunta \
fizer referência a algo discutido antes (uma carta, um cenário), use o histórico para \
entender o contexto."""


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
