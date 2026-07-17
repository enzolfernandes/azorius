"""Factory de provedores de IA (LLM + embeddings).

Este módulo é o ponto único de injeção de dependência da aplicação: ele traduz
a configuração do .env em um objeto `AIProvider` concreto (Gemini ou OpenAI)
com uma interface comum. Os demais services (`vector_db`, `llm_engine`)
recebem esse objeto como parâmetro e nunca sabem qual provedor está por trás —
trocar de provedor é alterar uma linha no .env, sem tocar em código.
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
    """Interface comum a todos os provedores de IA."""

    @abstractmethod
    def stream_chat(
        self, system_prompt: str, user_prompt: str, temperature: float | None = None
    ) -> Iterator[str]:
        """Gera a resposta do LLM em streaming, produzindo pedaços de texto.

        `temperature=0.0` deixa a saída determinística — útil para tarefas de
        extração estruturada; None usa o padrão do modelo.
        """

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Retorna um vetor de embedding para cada texto da lista."""


class GeminiProvider(AIProvider):
    CHAT_MODEL = "gemini-2.0-flash"
    EMBED_MODEL = "gemini-embedding-001"
    MAX_EMBED_RETRIES = 8
    DEFAULT_RETRY_DELAY = 60.0

    def __init__(self, api_key: str):
        # Import local: quem usa OpenAI não precisa do SDK do Google carregado.
        from google import genai

        self._client = genai.Client(api_key=api_key)

    def stream_chat(
        self, system_prompt: str, user_prompt: str, temperature: float | None = None
    ) -> Iterator[str]:
        from google.genai import types

        try:
            stream = self._client.models.generate_content_stream(
                model=self.CHAT_MODEL,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt, temperature=temperature
                ),
            )
            for chunk in stream:
                if chunk.text:
                    yield chunk.text
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
    CHAT_MODEL = "gpt-4o-mini"
    EMBED_MODEL = "text-embedding-3-small"

    def __init__(self, api_key: str):
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)

    def stream_chat(
        self, system_prompt: str, user_prompt: str, temperature: float | None = None
    ) -> Iterator[str]:
        try:
            kwargs = {"temperature": temperature} if temperature is not None else {}
            stream = self._client.chat.completions.create(
                model=self.CHAT_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                stream=True,
                **kwargs,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield delta
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


def get_provider(settings: Settings) -> AIProvider:
    """Factory: instancia o provedor concreto a partir das configurações."""
    if settings.llm_provider == "gemini":
        return GeminiProvider(api_key=settings.api_key)
    return OpenAIProvider(api_key=settings.api_key)
