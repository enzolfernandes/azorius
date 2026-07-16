"""Factory de provedores de IA (LLM + embeddings).

Este módulo é o ponto único de injeção de dependência da aplicação: ele traduz
a configuração do .env em um objeto `AIProvider` concreto (Gemini ou OpenAI)
com uma interface comum. Os demais services (`vector_db`, `llm_engine`)
recebem esse objeto como parâmetro e nunca sabem qual provedor está por trás —
trocar de provedor é alterar uma linha no .env, sem tocar em código.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator

from .config import Settings

# Limite de itens por requisição nas APIs de embedding (Gemini aceita 100).
EMBED_BATCH_SIZE = 100


class ProviderError(Exception):
    """Falha na comunicação com a API do provedor de IA."""


class AIProvider(ABC):
    """Interface comum a todos os provedores de IA."""

    @abstractmethod
    def stream_chat(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        """Gera a resposta do LLM em streaming, produzindo pedaços de texto."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Retorna um vetor de embedding para cada texto da lista."""


class GeminiProvider(AIProvider):
    CHAT_MODEL = "gemini-2.0-flash"
    # gemini-embedding-2: a cota diária gratuita é contada por modelo, e a do
    # gemini-embedding-001 foi esgotada durante a primeira ingestão.
    EMBED_MODEL = "gemini-embedding-2"

    def __init__(self, api_key: str):
        # Import local: quem usa OpenAI não precisa do SDK do Google carregado.
        from google import genai

        self._genai = genai
        self._client = genai.Client(api_key=api_key)

    def stream_chat(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        from google.genai import types

        try:
            stream = self._client.models.generate_content_stream(
                model=self.CHAT_MODEL,
                contents=user_prompt,
                config=types.GenerateContentConfig(system_instruction=system_prompt),
            )
            for chunk in stream:
                if chunk.text:
                    yield chunk.text
        except Exception as exc:
            raise ProviderError(f"Erro na API do Gemini: {exc}") from exc

    # A cota gratuita conta cada CONTEÚDO embedado (100/min), não cada request;
    # por isso o retry honra o retryDelay informado pela API no erro 429.
    MAX_EMBED_RETRIES = 8
    DEFAULT_RETRY_DELAY = 60.0

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[start : start + EMBED_BATCH_SIZE]
            got = self._embed_with_retry(batch)
            if len(got) == len(batch):
                vectors.extend(got)
                continue
            # Modelos como o gemini-embedding-2 ignoram o lote e devolvem um
            # único vetor por requisição; nesse caso embedamos item a item.
            # #region agent log
            import json as _json
            import time as _time
            with open("debug-844871.log", "a", encoding="utf-8") as _f:
                _f.write(_json.dumps({"sessionId": "844871", "runId": "post-fix", "hypothesisId": "A", "location": "providers.py:GeminiProvider.embed", "message": "modelo sem suporte a lote; fallback item a item", "data": {"batch_start": start, "enviados": len(batch), "recebidos": len(got)}, "timestamp": int(_time.time() * 1000)}) + "\n")
            # #endregion
            for text in batch:
                vectors.extend(self._embed_with_retry([text]))
        return vectors

    def _embed_with_retry(self, contents: list[str]) -> list[list[float]]:
        """Chama embed_content com retry que honra o retryDelay dos erros 429."""
        import re
        import time

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
                # #region agent log
                import json as _json
                with open("debug-844871.log", "a", encoding="utf-8") as _f:
                    _f.write(_json.dumps({"sessionId": "844871", "runId": "post-fix", "hypothesisId": "A", "location": "providers.py:GeminiProvider._embed_with_retry", "message": "429 na cota de embeddings; aguardando retry", "data": {"n_contents": len(contents), "attempt": attempt, "delay_s": delay}, "timestamp": int(time.time() * 1000)}) + "\n")
                # #endregion
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

    def stream_chat(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        try:
            stream = self._client.chat.completions.create(
                model=self.CHAT_MODEL,
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
