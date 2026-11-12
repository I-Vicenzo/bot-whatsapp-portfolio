"""Clientes de LLM. A API compatível com OpenAI cobre Groq, OpenAI e Ollama trocando só
`LLM_BASE_URL`, `LLM_API_KEY` e `LLM_MODEL`."""

from collections.abc import Callable
from typing import Protocol

import httpx


class LLMError(Exception):
    """Falha ou timeout do provedor. O bot segue como se a IA estivesse desligada."""


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        http: httpx.Client | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._model = model
        self._http = http or httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        self._http.close()

    def complete(self, system: str, user: str) -> str:
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": 800,  # modelos de raciocínio gastam tokens antes da resposta
        }
        try:
            response = self._http.post(self._url, json=body, headers=self._headers)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(type(exc).__name__) from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("resposta em formato inesperado") from exc
        if not isinstance(content, str):
            raise LLMError("resposta sem texto")
        return content


class FakeLLM:
    """Para testes: devolve respostas prontas ou executa uma função."""

    def __init__(self, reply: str | Callable[[str, str], str] = "") -> None:
        self._reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if callable(self._reply):
            return self._reply(system, user)
        return self._reply
