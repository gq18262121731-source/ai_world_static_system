import hashlib
import json
from collections.abc import AsyncIterator

import httpx

from app.ai.base import ASRProvider, ChatRequest, LLMProvider, TTSProvider
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class OpenAICompatLLM(LLMProvider):
    """兼容 OpenAI /chat/completions 协议的通用适配器。

    可直接用于 OpenAI、阿里云百炼兼容模式、DeepSeek、Moonshot 等服务。
    """

    name = "openai"

    def __init__(
        self, api_key: str = "", base_url: str = "", model: str = ""
    ) -> None:
        self.api_key = api_key or settings.llm_api_key
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def extra_payload(self) -> dict:
        """各家服务特有的请求参数，子类按需覆盖。"""
        return {}

    async def chat(self, request: ChatRequest) -> str:
        payload: dict = {
            "model": self.model,
            "messages": [m.to_dict() for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            **self.extra_payload(),
        }
        if request.json_mode:
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[str]:
        """SSE 流式：机器狗可以边收边念，老人不用盯着它发呆等整段话。"""
        payload: dict = {
            "model": self.model,
            "messages": [m.to_dict() for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": True,
            **self.extra_payload(),
        }
        if request.json_mode:
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    body = line[5:].strip()
                    if not body or body == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(body)
                    except json.JSONDecodeError:
                        continue
                    for choice in chunk.get("choices") or []:
                        delta = (choice.get("delta") or {}).get("content")
                        if delta:
                            yield delta


class OpenAICompatASR(ASRProvider):
    """兼容 OpenAI /audio/transcriptions 协议(Whisper 等)。"""

    name = "openai"

    def __init__(self, api_key: str = "", base_url: str = "", model: str = "") -> None:
        self.api_key = api_key or settings.asr_api_key
        self.base_url = (base_url or settings.asr_base_url).rstrip("/")
        self.model = model or settings.asr_model

    async def transcribe(self, audio: bytes, filename: str = "audio.wav") -> str:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                f"{self.base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": (filename, audio, "audio/wav")},
                data={"model": self.model, "language": "zh"},
            )
            resp.raise_for_status()
            return resp.json().get("text", "").strip()


class OpenAICompatTTS(TTSProvider):
    """兼容 OpenAI /audio/speech 协议。"""

    name = "openai"

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        voice: str = "alloy",
    ) -> None:
        self.api_key = api_key or settings.tts_api_key
        self.base_url = (base_url or settings.tts_base_url).rstrip("/")
        self.model = model or settings.tts_model
        self.voice = voice

    async def synthesize(self, text: str) -> str:
        digest = hashlib.md5(f"{self.model}:{text}".encode()).hexdigest()[:16]
        path = settings.media_dir / f"tts_{digest}.mp3"
        if path.exists():
            return f"/media/{path.name}"
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                f"{self.base_url}/audio/speech",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "input": text,
                    "voice": self.voice,
                    "response_format": "mp3",
                },
            )
            resp.raise_for_status()
            path.write_bytes(resp.content)
        return f"/media/{path.name}"
