"""Ingestão e consulta das Regras Abrangentes no ChromaDB.

Módulo puro: sem Streamlit. A dependência de embeddings é injetada como uma
função `embed_fn: list[str] -> list[list[float]]` (na prática, o método
`embed` de um `AIProvider`). Assim este módulo não conhece Gemini nem OpenAI —
apenas "algo que transforma textos em vetores" — e pode ser testado com um
embed_fn falso.
"""

import re
from collections.abc import Callable
from pathlib import Path

import chromadb

from .config import CHROMA_DIR, RULES_FILE
from .rules_setup import RulesDownloadError, ensure_rules_file

COLLECTION_NAME = "mtg_comprehensive_rules"

# Uma regra numerada no nível "100.1." inicia um novo chunk; sub-regras com
# letra ("100.1a") permanecem no mesmo chunk da regra-mãe, preservando o
# contexto completo de cada regra — melhor que chunks de tamanho fixo para
# texto normativo.
RULE_START_PATTERN = re.compile(r"^(\d{3}\.\d+)\.\s")
SUBRULE_PATTERN = re.compile(r"^\d{3}\.\d+[a-z]\s")

EmbedFn = Callable[[list[str]], list[list[float]]]

# Alinhado ao lote de embedding dos providers (100): cada lote embedado é
# gravado imediatamente no Chroma, então falhas de cota não perdem progresso.
ADD_BATCH_SIZE = 100


class VectorDBError(Exception):
    """Falha na ingestão ou consulta do banco vetorial."""


def parse_rules_chunks(rules_text: str) -> list[dict]:
    """Divide o texto das Comprehensive Rules em chunks semânticos.

    Cada chunk corresponde a uma regra numerada (ex.: 601.2) junto com todas
    as suas sub-regras (601.2a, 601.2b...). Retorna dicts:
        {"rule_number": "601.2", "text": "601.2. To cast a spell..."}
    """
    chunks: list[dict] = []
    current_number: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_number, current_lines
        if current_number and current_lines:
            chunks.append(
                {"rule_number": current_number, "text": "\n".join(current_lines).strip()}
            )
        current_number = None
        current_lines = []

    for line in rules_text.splitlines():
        stripped = line.strip()

        # O arquivo termina com Glossário e Créditos, que não são regras
        # numeradas; encerramos a ingestão ao chegar lá.
        if stripped == "Glossary" and chunks:
            break

        match = RULE_START_PATTERN.match(stripped)
        if match:
            flush()
            current_number = match.group(1)
            current_lines = [stripped]
        elif current_number and (SUBRULE_PATTERN.match(stripped) or stripped):
            current_lines.append(stripped)
        elif current_number and not stripped:
            # Linha em branco separa regras; mantém o chunk atual aberto até
            # a próxima regra numerada para não perder sub-regras.
            continue

    flush()
    return chunks


def initialize_db(
    embed_fn: EmbedFn,
    rules_path: Path = RULES_FILE,
    persist_dir: Path = CHROMA_DIR,
) -> chromadb.Collection:
    """Abre (ou cria) a coleção persistente de regras.

    Idempotente e retomável: cada lote é embedado e gravado imediatamente,
    então uma falha no meio (ex.: cota de API) não perde o progresso — a
    próxima execução ingere apenas as regras que ainda faltam.
    """
    client = chromadb.PersistentClient(path=str(persist_dir))
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )

    if not rules_path.exists():
        if collection.count() > 0:
            return collection
        try:
            ensure_rules_file(rules_path)
        except RulesDownloadError as exc:
            raise VectorDBError(
                f"Arquivo de regras não encontrado em {rules_path} e o download "
                f"automático falhou: {exc}. Execute 'python scripts/setup_rules.py' "
                "ou baixe o .txt em https://magic.wizards.com/en/rules."
            ) from exc

    rules_text = rules_path.read_text(encoding="utf-8")
    chunks = parse_rules_chunks(rules_text)
    if not chunks:
        raise VectorDBError("Nenhuma regra encontrada no arquivo — formato inesperado.")

    existing_ids = set(collection.get(include=[])["ids"]) if collection.count() > 0 else set()
    pending = [chunk for chunk in chunks if chunk["rule_number"] not in existing_ids]
    if not pending:
        return collection

    for start in range(0, len(pending), ADD_BATCH_SIZE):
        batch = pending[start : start + ADD_BATCH_SIZE]
        try:
            embeddings = embed_fn([chunk["text"] for chunk in batch])
        except Exception as exc:
            raise VectorDBError(f"Falha ao gerar embeddings das regras: {exc}") from exc
        collection.add(
            ids=[chunk["rule_number"] for chunk in batch],
            documents=[chunk["text"] for chunk in batch],
            embeddings=embeddings,
            metadatas=[{"rule_number": chunk["rule_number"]} for chunk in batch],
        )

    return collection


def query_rules(
    collection: chromadb.Collection,
    embed_fn: EmbedFn,
    question: str,
    n_results: int = 5,
) -> list[dict]:
    """Retorna as regras mais similares à pergunta.

    Cada resultado é {"rule_number": str, "text": str}.
    """
    try:
        question_embedding = embed_fn([question])[0]
        results = collection.query(
            query_embeddings=[question_embedding], n_results=n_results
        )
    except Exception as exc:
        raise VectorDBError(f"Falha na consulta ao banco vetorial: {exc}") from exc

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    return [
        {"rule_number": meta.get("rule_number", "?"), "text": doc}
        for doc, meta in zip(documents, metadatas)
    ]
