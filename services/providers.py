"""Factory de provedores de IA (LLM + embeddings).

Este módulo é o ponto único de injeção de dependência da aplicação: ele traduz
a configuração do .env (ou da UI) em um objeto `AIProvider` concreto
(Gemini, OpenAI ou Claude) com uma interface comum. Os demais services
(`vector_db`, `llm_engine`) recebem esse objeto como parâmetro e nunca sabem
qual provedor está por trás — trocar de provedor é alterar a configuração,
sem tocar em código.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator
import re
import time

from .config import Settings

# Limite de itens por requisição nas APIs de embedding.
EMBED_BATCH_SIZE = 100


class ProviderError(Exception):
    """Falha na comunicação com a API do provedor de IA."""


class AIProvider(ABC):
    """Interface comum a todos os provedores de IA.

    Dois papéis de modelo, com exigências opostas:
    - `stream_chat`: o ruling do juiz — usa o modelo mais capaz em raciocínio,
      priorizando qualidade sobre latência.
    - `quick_chat`: tarefas mecânicas (extração de cartas, reescrita de
      consulta) — usa um modelo rápido/barato com saída determinística.
    """

    @abstractmethod
    def stream_chat(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        """Gera o ruling em streaming, com o modelo de raciocínio."""

    @abstractmethod
    def quick_chat(self, system_prompt: str, user_prompt: str) -> str:
        """Resposta curta e determinística com o modelo utilitário."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Retorna um vetor de embedding para cada texto da lista."""


class GeminiProvider(AIProvider):
    RULING_MODEL = "gemini-2.5-flash"
    UTILITY_MODEL = "gemini-2.0-flash"
    EMBED_MODEL = "gemini-embedding-001"
    MAX_EMBED_RETRIES = 8
    DEFAULT_RETRY_DELAY = 60.0

    def __init__(self, api_key: str):
        # Import local: quem usa OpenAI não precisa do SDK do Google carregado.
        from google import genai

        self._client = genai.Client(api_key=api_key)

    def stream_chat(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        from google.genai import types

        try:
            stream = self._client.models.generate_content_stream(
                model=self.RULING_MODEL,
                contents=user_prompt,
                config=types.GenerateContentConfig(system_instruction=system_prompt),
            )
            for chunk in stream:
                if chunk.text:
                    yield chunk.text
        except Exception as exc:
            raise ProviderError(f"Erro na API do Gemini: {exc}") from exc

    def quick_chat(self, system_prompt: str, user_prompt: str) -> str:
        from google.genai import types

        try:
            response = self._client.models.generate_content(
                model=self.UTILITY_MODEL,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt, temperature=0.0
                ),
            )
            return response.text or ""
        except Exception as exc:
            raise ProviderError(f"Erro na API do Gemini: {exc}") from exc

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[start : start + EMBED_BATCH_SIZE]
            got = self._embed_with_retry(batch)
            if len(got) == len(batch):
                vectors.extend(got)
                continue
            # Alguns modelos de embedding do Gemini ignoram o lote e devolvem
            # um único vetor; nesse caso embedamos item a item.
            for text in batch:
                vectors.extend(self._embed_with_retry([text]))
        return vectors

    def _embed_with_retry(self, contents: list[str]) -> list[list[float]]:
        """Chama embed_content com retry que honra o retryDelay dos erros 429."""
        for attempt in range(self.MAX_EMBED_RETRIES):
            try:
                result = self._client.models.embed_content(
                    model=self.EMBED_MODEL, contents=contents
                )
                return [item.values for item in result.embeddings]
            except Exception as exc:
                message = str(exc)
                is_quota = "429" in message or "RESOURCE_EXHAUSTED" in message
                if not is_quota:
                    raise ProviderError(f"Erro ao gerar embeddings no Gemini: {exc}") from exc
                match = re.search(r"retry in ([\d.]+)s", message, re.IGNORECASE)
                delay = float(match.group(1)) + 2.0 if match else self.DEFAULT_RETRY_DELAY
                time.sleep(delay)
        raise ProviderError(
            "Cota de embeddings do Gemini esgotada mesmo após várias tentativas. "
            "Aguarde alguns minutos e recarregue a página para retomar a ingestão."
        )


class OpenAIProvider(AIProvider):
    # Modelo de raciocínio para o ruling: qualidade acima de latência.
    # Nota: modelos gpt-5+ não aceitam `temperature`; controlamos a
    # profundidade do raciocínio via `reasoning_effort`.
    RULING_MODEL = "gpt-5.5"
    RULING_REASONING_EFFORT = "high"
    UTILITY_MODEL = "gpt-4.1-mini"
    EMBED_MODEL = "text-embedding-3-small"

    def __init__(self, api_key: str):
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)

    def stream_chat(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        try:
            stream = self._client.chat.completions.create(
                model=self.RULING_MODEL,
                reasoning_effort=self.RULING_REASONING_EFFORT,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield delta
        except Exception as exc:
            raise ProviderError(f"Erro na API da OpenAI: {exc}") from exc

    def quick_chat(self, system_prompt: str, user_prompt: str) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self.UTILITY_MODEL,
                temperature=0.0,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            raise ProviderError(f"Erro na API da OpenAI: {exc}") from exc

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            vectors: list[list[float]] = []
            for start in range(0, len(texts), EMBED_BATCH_SIZE):
                batch = texts[start : start + EMBED_BATCH_SIZE]
                response = self._client.embeddings.create(
                    model=self.EMBED_MODEL, input=batch
                )
                vectors.extend(item.embedding for item in response.data)
            return vectors
        except Exception as exc:
            raise ProviderError(f"Erro ao gerar embeddings na OpenAI: {exc}") from exc


class ClaudeProvider(AIProvider):
    """Claude (Anthropic) para chat; embeddings locais (MiniLM via Chroma).

    A API da Anthropic não oferece embeddings. Para manter uma única chave
    (`ANTHROPIC_API_KEY`) e um índice Chroma separado (`chroma/claude`), o
    RAG usa a DefaultEmbeddingFunction do ChromaDB.
    """

    RULING_MODEL = "claude-sonnet-4-5"
    UTILITY_MODEL = "claude-haiku-4-5"
    # Respostas de juiz podem ser longas (fundamentação + listas).
    RULING_MAX_TOKENS = 8192
    UTILITY_MAX_TOKENS = 1024

    def __init__(self, api_key: str):
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key)
        self._embedder = None

    def stream_chat(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        try:
            with self._client.messages.stream(
                model=self.RULING_MODEL,
                max_tokens=self.RULING_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            ) as stream:
                for text in stream.text_stream:
                    if text:
                        yield text
        except Exception as exc:
            raise ProviderError(f"Erro na API do Claude: {exc}") from exc

    def quick_chat(self, system_prompt: str, user_prompt: str) -> str:
        try:
            response = self._client.messages.create(
                model=self.UTILITY_MODEL,
                max_tokens=self.UTILITY_MAX_TOKENS,
                temperature=0.0,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            parts = [
                block.text
                for block in response.content
                if getattr(block, "type", None) == "text" and getattr(block, "text", None)
            ]
            return "".join(parts)
        except Exception as exc:
            raise ProviderError(f"Erro na API do Claude: {exc}") from exc

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            if self._embedder is None:
                from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

                self._embedder = DefaultEmbeddingFunction()
            vectors: list[list[float]] = []
            for start in range(0, len(texts), EMBED_BATCH_SIZE):
                batch = texts[start : start + EMBED_BATCH_SIZE]
                vectors.extend(self._embedder(batch))
            return vectors
        except Exception as exc:
            raise ProviderError(f"Erro ao gerar embeddings locais (Claude/RAG): {exc}") from exc


def get_provider(settings: Settings) -> AIProvider:
    """Factory: instancia o provedor concreto a partir das configurações."""
    if settings.llm_provider == "gemini":
        return GeminiProvider(api_key=settings.api_key)
    if settings.llm_provider == "claude":
        return ClaudeProvider(api_key=settings.api_key)
    return OpenAIProvider(api_key=settings.api_key)
