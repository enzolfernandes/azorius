"""Montagem do prompt de juiz e geração da resposta via LLM.

Módulo puro: sem Streamlit. O provedor de LLM é injetado como parâmetro
(`AIProvider`), e a resposta é devolvida como um generator de strings — a
camada de UI decide como consumir o streaming (st.write_stream hoje,
StreamingResponse no FastAPI amanhã), sem que este módulo saiba quem o chama.
"""

from collections.abc import Iterator

from .providers import AIProvider

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
qualquer versão impressa que o usuário mencione.
5. Responda em Português do Brasil, mas mantenha termos de jogo consagrados em inglês \
(stack, trigger, oracle text, etc.) quando a tradução gerar ambiguidade.
6. Estruture a resposta: ruling direto primeiro, fundamentação com citações das regras depois."""


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
    card_data: list[dict], rules_context: list[dict], user_question: str
) -> str:
    """Monta o prompt de contexto injetando regras, cartas e a pergunta."""
    return f"""## CONTEXTO — COMPREHENSIVE RULES (trechos oficiais recuperados)
{_format_rules_section(rules_context)}

## CONTEXTO — CARTAS CITADAS (dados oficiais do Scryfall)
{_format_cards_section(card_data)}

## PERGUNTA DO JOGADOR
{user_question}

Dê seu ruling seguindo estritamente as regras de conduta."""


def generate_judge_ruling(
    provider: AIProvider,
    card_data: list[dict],
    rules_context: list[dict],
    user_question: str,
) -> Iterator[str]:
    """Gera o ruling do juiz em streaming (generator de pedaços de texto).

    O `provider` é injetado pelo chamador — este módulo não sabe se é Gemini
    ou OpenAI, apenas usa a interface comum `stream_chat`.
    """
    user_prompt = build_user_prompt(card_data, rules_context, user_question)
    yield from provider.stream_chat(SYSTEM_PROMPT, user_prompt)
