"""Transcrição de áudio (Whisper) por API compatível com OpenAI, como a do Groq."""

import httpx

from app.ai.client import LLMError

MAX_AUDIO_BYTES = 10 * 1024 * 1024


class Transcriber:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        http: httpx.Client | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/audio/transcriptions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._model = model
        self._http = http or httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        self._http.close()

    def transcribe(self, audio: bytes, mime_type: str) -> str:
        if not audio or len(audio) > MAX_AUDIO_BYTES:
            raise LLMError("áudio vazio ou grande demais")
        extension = mime_type.split("/")[-1].split(";")[0].strip() or "ogg"
        try:
            response = self._http.post(
                self._url,
                headers=self._headers,
                data={"model": self._model, "language": "pt", "response_format": "json"},
                files={"file": (f"audio.{extension}", audio, mime_type)},
            )
            response.raise_for_status()
            text = response.json()["text"]
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(type(exc).__name__) from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMError("resposta em formato inesperado") from exc
        if not isinstance(text, str) or not text.strip():
            raise LLMError("transcrição vazia")
        return " ".join(text.split())
