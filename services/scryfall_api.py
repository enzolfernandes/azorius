"""Consulta de cartas na API pública do Scryfall.

Módulo puro: sem Streamlit e sem estado global. Recebe um nome de carta e
devolve um dicionário limpo com apenas os campos que o restante da aplicação
precisa (imagem, texto de oracle e rulings).
"""

import time

import requests

SCRYFALL_NAMED_URL = "https://api.scryfall.com/cards/named"
REQUEST_TIMEOUT = 10

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


def _fetch_rulings(rulings_uri: str) -> list[str]:
    """Busca os rulings oficiais da carta; falha silenciosa vira lista vazia."""
    try:
        time.sleep(RATE_LIMIT_DELAY)
        response = requests.get(rulings_uri, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        return [item["comment"] for item in data.get("data", []) if item.get("comment")]
    except (requests.RequestException, ValueError, KeyError):
        # Rulings são complementares: a ausência deles não deve derrubar a consulta.
        return []


def fetch_card_data(card_name: str) -> dict | None:
    """Busca uma carta por nome (busca fuzzy) e retorna um dicionário limpo.

    Retorna None quando a carta não é encontrada ou há erro de rede.
    Estrutura do retorno:
        {name, mana_cost, type_line, oracle_text, image_url, rulings}
    """
    try:
        time.sleep(RATE_LIMIT_DELAY)
        response = requests.get(
            SCRYFALL_NAMED_URL,
            params={"fuzzy": card_name},
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        card = response.json()
    except (requests.RequestException, ValueError):
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
