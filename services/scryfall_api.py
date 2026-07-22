"""Consulta de cartas na API pública do Scryfall.

Módulo puro: sem Streamlit e sem estado global. Recebe um nome de carta e
devolve um dicionário limpo com apenas os campos que o restante da aplicação
precisa (imagem, texto de oracle e rulings).
"""

import re
import time
import unicodedata

import requests

SCRYFALL_NAMED_URL = "https://api.scryfall.com/cards/named"
SCRYFALL_SEARCH_URL = "https://api.scryfall.com/cards/search"
REQUEST_TIMEOUT = 10
NETWORK_RETRIES = 2

# O Scryfall pede um User-Agent identificável e no máximo ~10 req/s.
HEADERS = {
    "User-Agent": "AzoriusJudge/0.1 (prototipo local de juiz de regras)",
    "Accept": "application/json",
}
RATE_LIMIT_DELAY = 0.1

# Tokens irrelevantes ao cruzar nome resolvido × pergunta do jogador.
_CONTENT_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "and",
        "or",
        "to",
        "from",
        "in",
        "on",
        "with",
        "for",
        "by",
        "at",
        "as",
    }
)

# Preposições/artigos/ruído de pergunta (PT/EN) ao extrair trechos candidatos a nome.
_PHRASE_FILLERS = _CONTENT_STOPWORDS | frozenset(
    {
        "o",
        "os",
        "as",
        "um",
        "uma",
        "uns",
        "umas",
        "de",
        "da",
        "do",
        "das",
        "dos",
        "em",
        "no",
        "na",
        "nos",
        "nas",
        "com",
        "sem",
        "por",
        "para",
        "pelo",
        "pela",
        "que",
        "se",
        "eu",
        "meu",
        "minha",
        "ele",
        "ela",
        "quais",
        "qual",
        "top",
        "carta",
        "cartas",
        "combina",
        "combinam",
        "sobre",
        "como",
        "quando",
        "onde",
        "porque",
        "porquê",
        "what",
        "which",
        "who",
        "how",
        "when",
        "where",
        "why",
        "card",
        "cards",
        "my",
        "his",
        "her",
        "their",
    }
)

# Ligaduras permitidas no meio de um nome composto (EN/PT).
_NAME_LINKERS = frozenset({"of", "the", "a", "an", "and", "de", "da", "do", "das", "dos"})

# Tokens de palavra com acentos (ex.: "Grão"); hífen continua separador.
_WORD_RE = re.compile(r"[\w']+", re.UNICODE)


def _extract_image_url(card: dict) -> str | None:
    """Resolve a URL da imagem, cobrindo cartas de dupla face (card_faces)."""
    if "image_uris" in card:
        return card["image_uris"].get("normal")
    faces = card.get("card_faces") or []
    if faces and "image_uris" in faces[0]:
        return faces[0]["image_uris"].get("normal")
    return None


def _extract_oracle_text(card: dict) -> str:
    """Resolve o oracle_text, concatenando as faces quando a carta tem duas."""
    if card.get("oracle_text"):
        return card["oracle_text"]
    faces = card.get("card_faces") or []
    parts = [
        f"{face.get('name', '')}: {face.get('oracle_text', '')}"
        for face in faces
        if face.get("oracle_text")
    ]
    return "\n---\n".join(parts)


def _get(url: str, params: dict | None = None) -> requests.Response | None:
    """GET com rate limit e retry para erros transitórios de rede."""
    for _ in range(NETWORK_RETRIES + 1):
        try:
            time.sleep(RATE_LIMIT_DELAY)
            return requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        except requests.RequestException:
            continue
    return None


def _named_lookup(params: dict) -> dict | None:
    """Consulta /cards/named (fuzzy ou exact); None se não encontrada ou erro."""
    response = _get(SCRYFALL_NAMED_URL, params)
    if response is None or response.status_code == 404:
        return None
    try:
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError):
        return None


def _search_multilingual(name: str) -> dict | None:
    """Busca pelo nome impresso em qualquer idioma (ex.: "Vidas Paralelas").

    Cobre cartas citadas pelo nome oficial traduzido, que a busca fuzzy
    (restrita a nomes em inglês) não encontra. Só aceita correspondência
    clara do nome impresso — não o primeiro hit genérico da busca.
    """
    response = _get(
        SCRYFALL_SEARCH_URL,
        {"q": f'"{name}"', "include_multilingual": "true", "unique": "cards"},
    )
    if response is None or response.status_code != 200:
        return None
    try:
        results = response.json().get("data", [])
    except ValueError:
        return None
    if not results:
        return None

    lowered = _fold(name.strip())
    for card in results:
        printed = _fold(card.get("printed_name") or "")
        english = _fold(card.get("name") or "")
        if printed == lowered or english == lowered:
            return card

    # Um único resultado com forte sobreposição de tokens ainda é confiável.
    if len(results) == 1:
        only = results[0]
        candidate = only.get("printed_name") or only.get("name") or ""
        query_tokens = _content_tokens(name)
        if query_tokens and query_tokens <= _content_tokens(candidate):
            return only
    return None


def _fetch_rulings(rulings_uri: str) -> list[str]:
    """Busca os rulings oficiais da carta; falha silenciosa vira lista vazia."""
    response = _get(rulings_uri)
    if response is None:
        return []
    try:
        response.raise_for_status()
        data = response.json()
        return [item["comment"] for item in data.get("data", []) if item.get("comment")]
    except (requests.RequestException, ValueError, KeyError):
        # Rulings são complementares: a ausência deles não deve derrubar a consulta.
        return []


def _fold(text: str) -> str:
    """Normaliza para comparação: minúsculas e sem acentos."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _content_tokens(text: str) -> set[str]:
    """Tokens relevantes de um nome/pergunta (ignora artigos e preposições)."""
    return {
        _fold(token)
        for token in _WORD_RE.findall(text)
        if _fold(token) not in _CONTENT_STOPWORDS and len(_fold(token)) > 1
    }


def _invented_tokens(resolved_name: str, question: str) -> set[str]:
    """Tokens do nome resolvido que não aparecem na pergunta do jogador."""
    return _content_tokens(resolved_name) - _content_tokens(question)


def _card_like_phrase(question: str, anchors: set[str]) -> str | None:
    """Extrai da pergunta o trecho mais longo que parece um nome e contém âncoras.

    Usado para recuperar o texto original do jogador quando o LLM substitui uma
    carta por outra parecida (ex.: "Hazezon Tamar" no lugar de
    "Hazezon Shaper of Sand").
    """
    if not anchors:
        return None

    words = _WORD_RE.findall(question)
    if not words:
        return None

    folded_anchors = {_fold(a) for a in anchors}
    best: list[str] = []
    i = 0
    while i < len(words):
        lowered = _fold(words[i])
        if lowered in _PHRASE_FILLERS or lowered in _NAME_LINKERS:
            i += 1
            continue

        run = [words[i]]
        j = i + 1
        while j < len(words):
            nxt = _fold(words[j])
            # Ligaduras ("of", "de") vêm antes dos fillers: fazem parte de
            # nomes compostos em inglês e português.
            if nxt in _NAME_LINKERS:
                if j + 1 < len(words) and _fold(words[j + 1]) not in _PHRASE_FILLERS:
                    run.append(words[j])
                    j += 1
                    continue
                break
            if nxt in _PHRASE_FILLERS:
                break
            run.append(words[j])
            j += 1

        run_tokens = _content_tokens(" ".join(run))
        if folded_anchors & run_tokens and len(run) > len(best):
            best = run
        i = j if j > i else i + 1

    return " ".join(best) if best else None


def _lookup_card_raw(card_name: str) -> dict | None:
    """Resolve um nome via fuzzy inglês e/ou busca multilíngue.

    Nomes com caracteres não-ASCII (ex.: "Sede de Grão-vampiro") tentam a
    busca multilíngue primeiro — a fuzzy em inglês costuma falhar ou acertar
    cartas erradas com nomes curtos parecidos.
    """
    prefer_foreign = any(ord(ch) > 127 for ch in card_name)

    def from_foreign() -> dict | None:
        foreign = _search_multilingual(card_name)
        if not foreign:
            return None
        return _named_lookup({"exact": foreign.get("name", "")}) or foreign

    if prefer_foreign:
        card = from_foreign()
        if card is not None:
            return card

    card = _named_lookup({"fuzzy": card_name})
    if card is None and not prefer_foreign:
        card = from_foreign()
    return card


def _reconcile_with_question(card: dict, card_name: str, question: str) -> dict:
    """Troca o hit do Scryfall se ele inventou tokens ausentes na pergunta.

    Apelidos curtos ("bolt" → Lightning Bolt) continuam válidos. Substituições
    erradas do LLM ("Hazezon Tamar" no lugar de "Hazezon Shaper of Sand") são
    corrigidas. Traduções PT→EN legítimas NÃO são revertidas: se o nome
    consultado já está contido na pergunta, o resultado do Scryfall prevalece
    (evita "Sede de Grão-vampiro" → Bloodchief's Thirst virar Thirst/Sede).
    """
    query_tokens = _content_tokens(card_name)
    question_tokens = _content_tokens(question)
    if query_tokens and query_tokens <= question_tokens:
        return card

    resolved_name = card.get("name", "")
    invented = _invented_tokens(resolved_name, question)
    if not invented:
        return card

    anchors = query_tokens & question_tokens
    if not anchors:
        anchors = _content_tokens(resolved_name) & question_tokens
    phrase = _card_like_phrase(question, anchors)
    if not phrase or _fold(phrase) == _fold(card_name.strip()):
        return card

    # Não trocar por um trecho mais curto/ambíguo que o nome original consultado.
    if len(_content_tokens(phrase)) < len(query_tokens):
        return card

    alternative = _lookup_card_raw(phrase)
    if alternative is None:
        return card

    alt_invented = _invented_tokens(alternative.get("name", ""), question)
    if len(alt_invented) < len(invented):
        return alternative
    return card


def fetch_card_data(card_name: str, question: str | None = None) -> dict | None:
    """Busca uma carta por nome e retorna um dicionário limpo.

    Estratégia: busca fuzzy por nome em inglês e/ou busca multilíngue pelo
    nome impresso (aceita nomes oficiais traduzidos, como "Vidas Paralelas").
    Quando `question` é informada, rejeita hits que introduzem tokens ausentes
    na pergunta e tenta recuperar o nome a partir do texto do jogador.
    Retorna None quando a carta não é encontrada.
    Estrutura do retorno:
        {name, mana_cost, type_line, oracle_text, image_url, rulings}
    """
    card = _lookup_card_raw(card_name)
    if card is None:
        return None
    if question:
        card = _reconcile_with_question(card, card_name, question)

    rulings_uri = card.get("rulings_uri")
    return {
        "name": card.get("name", card_name),
        "mana_cost": card.get("mana_cost", ""),
        "type_line": card.get("type_line", ""),
        "oracle_text": _extract_oracle_text(card),
        "image_url": _extract_image_url(card),
        "rulings": _fetch_rulings(rulings_uri) if rulings_uri else [],
    }


def _parse_usd(card: dict) -> float | None:
    """Extrai o preço USD do payload Scryfall; None se indisponível."""
    raw = (card.get("prices") or {}).get("usd")
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _normalize_pool_card(card: dict) -> dict:
    """Normaliza uma carta Scryfall para o pipeline do Deckbuilder."""
    return {
        "name": card.get("name", ""),
        "color_identity": list(card.get("color_identity") or []),
        "cmc": float(card.get("cmc") or 0),
        "prices": {"usd": _parse_usd(card)},
        "type_line": card.get("type_line", ""),
        "oracle_text": _extract_oracle_text(card),
        "image_url": _extract_image_url(card),
    }


def fetch_commander(name: str) -> dict | None:
    """Resolve um comandante por nome (fuzzy/multilíngue) para o Deckbuilder.

    Retorna None se a carta não for encontrada. Campos:
        name, color_identity, cmc, type_line, image_url, oracle_text
    """
    card = _lookup_card_raw(name)
    if card is None:
        return None
    return {
        "name": card.get("name", name),
        "color_identity": list(card.get("color_identity") or []),
        "cmc": float(card.get("cmc") or 0),
        "type_line": card.get("type_line", ""),
        "image_url": _extract_image_url(card),
        "oracle_text": _extract_oracle_text(card),
    }


def fetch_card_market_info(name: str) -> dict | None:
    """Resolve nome + mana_cost (+ usd Scryfall só como metadado; orçamento BR usa LigaMagic)."""
    card = _lookup_card_raw(name)
    if card is None:
        return None
    return {
        "name": card.get("name", name),
        "mana_cost": card.get("mana_cost", "") or "",
        "cmc": float(card.get("cmc") or 0),
        "usd": _parse_usd(card),
        "type_line": card.get("type_line", ""),
        "oracle_text": _extract_oracle_text(card),
    }


def fetch_card_image_url(name: str) -> str | None:
    """Resolve só a URL da imagem Scryfall (preview no chat)."""
    query = (name or "").strip()
    if not query:
        return None
    card = _lookup_card_raw(query)
    if card is None:
        return None
    return _extract_image_url(card)


def fetch_commander_card_pool(
    commander_name: str, *, max_cards: int = 400
) -> list[dict]:
    """Busca candidatos legais em Commander para o comandante informado.

    Usa a sintaxe Scryfall `commander:"Nome"` (identidade legal, exclusão do
    próprio comandante na prática via dedupe). Pagina até `max_cards`.
    Cada item: name, color_identity, cmc, prices.usd, type_line, oracle_text.
    """
    if max_cards <= 0:
        return []

    # unique=cards evita reprints; order=edhrec prioriza staples do formato.
    query = f'commander:"{commander_name}" -is:funny game:paper'
    response = _get(
        SCRYFALL_SEARCH_URL,
        {"q": query, "unique": "cards", "order": "edhrec"},
    )
    if response is None or response.status_code != 200:
        return []

    pool: list[dict] = []
    seen_names: set[str] = set()
    commander_key = commander_name.strip().lower()

    while response is not None and response.status_code == 200:
        try:
            payload = response.json()
        except ValueError:
            break
        for raw in payload.get("data", []):
            normalized = _normalize_pool_card(raw)
            key = normalized["name"].strip().lower()
            if not key or key in seen_names or key == commander_key:
                continue
            seen_names.add(key)
            pool.append(normalized)
            if len(pool) >= max_cards:
                return pool

        if not payload.get("has_more") or not payload.get("next_page"):
            break
        response = _get(payload["next_page"])

    return pool
