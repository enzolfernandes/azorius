"""Factory de provedores de IA (LLM + embeddings).

Este módulo é o ponto único de injeção de dependência da aplicação: ele traduz
a configuração do .env (ou da UI) em um objeto `AIProvider` concreto
(Gemini, OpenAI ou Claude) com uma interface comum. Os demais services
(`vector_db`, `llm_engine`) recebem esse objeto como parâmetro e nunca sabem
qual provedor está por trás — trocar de provedor é alterar a configuração,
sem tocar em código.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
import json
import re
import time
from typing import Any

from .config import Settings

# Limite de itens por requisição nas APIs de embedding.
EMBED_BATCH_SIZE = 100

# Evita loops infinitos de function calling no Deckbuilder.
MAX_TOOL_ROUNDS = 6


class ProviderError(Exception):
    """Falha na comunicação com a API do provedor de IA."""


class AIProvider(ABC):
    """Interface comum a todos os provedores de IA.

    Dois papéis de modelo, com exigências opostas:
    - `stream_chat`: o ruling do juiz — usa o modelo mais capaz em raciocínio,
      priorizando qualidade sobre latência.
    - `quick_chat`: tarefas mecânicas (extração de cartas, reescrita de
      consulta) — usa um modelo rápido/barato com saída determinística.
    - `chat_with_tools`: conversa com function calling (Deckbuilder agentic).
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

    @abstractmethod
    def chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict],
        execute_tool: Callable[[str, dict], str],
    ) -> str:
        """Chat com tools: executa tool_calls e devolve o texto final do assistente."""


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

    def chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict],
        execute_tool: Callable[[str, dict], str],
    ) -> str:
        from google.genai import types

        declarations = []
        for tool in tools:
            declarations.append(
                types.FunctionDeclaration(
                    name=tool["name"],
                    description=tool.get("description", ""),
                    parameters=tool.get("parameters") or {"type": "object", "properties": {}},
                )
            )
        gemini_tools = [types.Tool(function_declarations=declarations)] if declarations else None

        contents: list[types.Content] = []
        for msg in messages:
            role = "model" if msg.get("role") == "assistant" else "user"
            contents.append(
                types.Content(
                    role=role,
                    parts=[types.Part.from_text(text=msg.get("content") or "")],
                )
            )

        try:
            for _ in range(MAX_TOOL_ROUNDS):
                response = self._client.models.generate_content(
                    model=self.RULING_MODEL,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        tools=gemini_tools,
                    ),
                )
                candidate = (response.candidates or [None])[0]
                if candidate is None or not candidate.content:
                    return (response.text or "").strip()

                parts = list(candidate.content.parts or [])
                function_calls = [
                    p.function_call for p in parts if getattr(p, "function_call", None)
                ]
                if not function_calls:
                    text_parts = [
                        p.text for p in parts if getattr(p, "text", None)
                    ]
                    if text_parts:
                        return "".join(text_parts).strip()
                    return (response.text or "").strip()

                contents.append(candidate.content)
                result_parts = []
                for call in function_calls:
                    args = dict(call.args or {})
                    result = execute_tool(call.name, args)
                    result_parts.append(
                        types.Part.from_function_response(
                            name=call.name,
                            response={"result": result},
                        )
                    )
                contents.append(types.Content(role="user", parts=result_parts))

            return (
                "Não consegui concluir a consulta de ferramentas a tempo. "
                "Tente novamente com um pacote menor."
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"Erro na API do Gemini (tools): {exc}") from exc


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

    def chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict],
        execute_tool: Callable[[str, dict], str],
    ) -> str:
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters")
                    or {"type": "object", "properties": {}},
                },
            }
            for tool in tools
        ]
        thread: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *[
                {"role": m["role"], "content": m.get("content") or ""}
                for m in messages
                if m.get("role") in ("user", "assistant")
            ],
        ]
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                kwargs: dict[str, Any] = {
                    "model": self.RULING_MODEL,
                    "messages": thread,
                    "tools": openai_tools,
                }
                # gpt-5+ usa reasoning_effort; temperature pode falhar.
                if self.RULING_MODEL.startswith("gpt-5"):
                    kwargs["reasoning_effort"] = self.RULING_REASONING_EFFORT
                response = self._client.chat.completions.create(**kwargs)
                message = response.choices[0].message
                tool_calls = message.tool_calls or []
                if not tool_calls:
                    return (message.content or "").strip()

                thread.append(
                    {
                        "role": "assistant",
                        "content": message.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments or "{}",
                                },
                            }
                            for tc in tool_calls
                        ],
                    }
                )
                for tc in tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    if not isinstance(args, dict):
                        args = {}
                    result = execute_tool(tc.function.name, args)
                    thread.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        }
                    )

            return (
                "Não consegui concluir a consulta de ferramentas a tempo. "
                "Tente novamente com um pacote menor."
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"Erro na API da OpenAI (tools): {exc}") from exc


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

    def chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict],
        execute_tool: Callable[[str, dict], str],
    ) -> str:
        claude_tools = [
            {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "input_schema": tool.get("parameters")
                or {"type": "object", "properties": {}},
            }
            for tool in tools
        ]
        thread: list[dict[str, Any]] = [
            {"role": m["role"], "content": m.get("content") or ""}
            for m in messages
            if m.get("role") in ("user", "assistant")
        ]
        # Claude exige que a conversa comece com user.
        if not thread or thread[0]["role"] != "user":
            thread.insert(0, {"role": "user", "content": "(início da oficina)"})

        try:
            for _ in range(MAX_TOOL_ROUNDS):
                response = self._client.messages.create(
                    model=self.RULING_MODEL,
                    max_tokens=self.RULING_MAX_TOKENS,
                    system=system_prompt,
                    tools=claude_tools,
                    messages=thread,
                )
                tool_uses = [
                    block
                    for block in response.content
                    if getattr(block, "type", None) == "tool_use"
                ]
                text_parts = [
                    block.text
                    for block in response.content
                    if getattr(block, "type", None) == "text"
                    and getattr(block, "text", None)
                ]
                if response.stop_reason != "tool_use" or not tool_uses:
                    return "".join(text_parts).strip()

                thread.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in tool_uses:
                    args = dict(getattr(block, "input", None) or {})
                    result = execute_tool(block.name, args)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }
                    )
                thread.append({"role": "user", "content": tool_results})

            return (
                "Não consegui concluir a consulta de ferramentas a tempo. "
                "Tente novamente com um pacote menor."
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"Erro na API do Claude (tools): {exc}") from exc


def get_provider(settings: Settings) -> AIProvider:
    """Factory: instancia o provedor concreto a partir das configurações."""
    if settings.llm_provider == "gemini":
        return GeminiProvider(api_key=settings.api_key)
    if settings.llm_provider == "claude":
        return ClaudeProvider(api_key=settings.api_key)
    return OpenAIProvider(api_key=settings.api_key)
