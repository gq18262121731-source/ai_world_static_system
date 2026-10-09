import base64
import hashlib

import httpx

from app.ai.base import ASRProvider, TTSProvider
from app.ai.openai_compat import TIMEOUT, OpenAICompatLLM
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

DASHSCOPE_COMPATIBLE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DASHSCOPE_NATIVE = "https://dashscope.aliyuncs.com/api/v1"


class DashScopeLLM(OpenAICompatLLM):
    """阿里云百炼(通义千问)。兼容模式与 OpenAI 协议一致。"""

    name = "dashscope"

    def __init__(self, api_key: str = "", model: str = "") -> None:
        super().__init__(
            api_key=api_key or settings.llm_api_key,
            base_url=settings.llm_base_url or DASHSCOPE_COMPATIBLE,
            model=model or settings.llm_model or "qwen-plus",
        )

    def extra_payload(self) -> dict:
        # Qwen3 系列默认开思考模式，同一个请求会从 1 秒变成 13 秒以上；
        # 老人站在机器狗面前等回答，必须关掉
        return {"enable_thinking": False}


class DashScopeASR(ASRProvider):
    """使用 qwen-audio-asr 做语音识别(多模态对话接口, 音频以 base64 传入)。"""

    name = "dashscope"

    def __init__(self, api_key: str = "", model: str = "") -> None:
        self.api_key = api_key or settings.asr_api_key or settings.llm_api_key
        self.model = model or "qwen-audio-asr"

    async def transcribe(self, audio: bytes, filename: str = "audio.wav") -> str:
        audio_b64 = base64.b64encode(audio).decode()
        payload = {
            "model": self.model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"audio": f"data:audio/wav;base64,{audio_b64}"}],
                    }
                ]
            },
        }
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                f"{DASHSCOPE_NATIVE}/services/aigc/multimodal-generation/generation",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        choices = data.get("output", {}).get("choices", [])
        if not choices:
            return ""
        content = choices[0].get("message", {}).get("content", [])
        for item in content:
            if isinstance(item, dict) and "text" in item:
                return str(item["text"]).strip()
        return ""


class DashScopeTTS(TTSProvider):
    """使用 qwen-tts 合成语音, 接口返回音频 URL, 这里下载后本地提供访问。"""

    name = "dashscope"

    def __init__(self, api_key: str = "", model: str = "", voice: str = "") -> None:
        self.api_key = api_key or settings.tts_api_key or settings.llm_api_key
        self.model = model or settings.tts_model or "qwen-tts"
        self.voice = voice or settings.tts_voice or "Cherry"

    async def synthesize(self, text: str) -> str:
        digest = hashlib.md5(f"{self.model}:{self.voice}:{text}".encode()).hexdigest()[:16]
        path = settings.media_dir / f"tts_{digest}.wav"
        if path.exists():
            return f"/media/{path.name}"

        payload = {
            "model": self.model,
            "input": {"text": text, "voice": self.voice},
        }
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                f"{DASHSCOPE_NATIVE}/services/aigc/multimodal-generation/generation",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            audio_url = (
                resp.json().get("output", {}).get("audio", {}).get("url", "")
            )
            if not audio_url:
                return ""
            audio_resp = await client.get(audio_url)
            audio_resp.raise_for_status()
            path.write_bytes(audio_resp.content)
        return f"/media/{path.name}"
