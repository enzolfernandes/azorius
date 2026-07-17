"""Consulta de cartas na API pública do Scryfall.

Módulo puro: sem Streamlit e sem estado global. Recebe um nome de carta e
devolve um dicionário limpo com apenas os campos que o restante da aplicação
precisa (imagem, texto de oracle e rulings).
"""

import time

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
    (restrita a nomes em inglês) não encontra.
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

    # Prioriza correspondência exata do nome impresso; senão, o primeiro hit.
    lowered = name.strip().lower()
    for card in results:
        printed = (card.get("printed_name") or "").strip().lower()
        if printed == lowered or card.get("name", "").strip().lower() == lowered:
            return card
    return results[0]


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


def fetch_card_data(card_name: str) -> dict | None:
    """Busca uma carta por nome e retorna um dicionário limpo.

    Estratégia em duas etapas: busca fuzzy por nome em inglês e, se falhar,
    busca multilíngue pelo nome impresso (aceita nomes oficiais traduzidos,
    como "Vidas Paralelas"). Retorna None quando a carta não é encontrada.
    Estrutura do retorno:
        {name, mana_cost, type_line, oracle_text, image_url, rulings}
    """
    card = _named_lookup({"fuzzy": card_name})
    if card is None:
        foreign = _search_multilingual(card_name)
        if foreign:
            # Re-busca pelo nome oficial em inglês para obter a impressão
            # canônica (oracle text e imagem em inglês).
            card = _named_lookup({"exact": foreign.get("name", "")}) or foreign
    if card is None:
        return None

    rulings_uri = card.get("rulings_uri")
    return {
        "name": card.get("name", card_name),
        "mana_cost": card.get("mana_cost", ""),
        "type_line": card.get("type_line", ""),
        "oracle_text": _extract_oracle_text(card),
        "image_url": _extract_image_url(card),
        "rulings": _fetch_rulings(rulings_uri) if rulings_uri else [],
    }
