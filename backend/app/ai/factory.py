from functools import lru_cache

from app.ai.base import ASRProvider, ChatRequest, LLMProvider, TTSProvider
from app.ai.mock import MockASR, MockLLM, MockTTS
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class ResilientLLM(LLMProvider):
    """真实模型调用失败时自动退回 Mock, 保证演示流程不中断。"""

    def __init__(self, primary: LLMProvider) -> None:
        self.primary = primary
        self.fallback = MockLLM()
        self.name = primary.name
        self.last_error = ""
        self.fallback_count = 0

    async def chat(self, request: ChatRequest) -> str:
        try:
            content = await self.primary.chat(request)
            if content and content.strip():
                return content
            raise ValueError("模型返回空内容")
        except Exception as exc:
            self.last_error = str(exc)
            self.fallback_count += 1
            logger.warning("LLM(%s) 调用失败, 退回 Mock: %s", self.primary.name, exc)
            return await self.fallback.chat(request)

    async def chat_stream(self, request: ChatRequest):
        produced = False
        try:
            async for chunk in self.primary.chat_stream(request):
                produced = True
                yield chunk
        except Exception as exc:
            self.last_error = str(exc)
            self.fallback_count += 1
            logger.warning("LLM(%s) 流式调用失败, 退回 Mock: %s", self.primary.name, exc)
            if produced:
                # 话已经说了一半，不能再换成另一段；只能就此收尾
                return
            async for chunk in self.fallback.chat_stream(request):
                yield chunk
            return
        if not produced:
            async for chunk in self.fallback.chat_stream(request):
                yield chunk


class ResilientASR(ASRProvider):
    def __init__(self, primary: ASRProvider) -> None:
        self.primary = primary
        self.fallback = MockASR()
        self.name = primary.name
        self.last_error = ""

    async def transcribe(self, audio: bytes, filename: str = "audio.wav") -> str:
        try:
            text = await self.primary.transcribe(audio, filename)
            if text:
                return text
            raise ValueError("识别结果为空")
        except Exception as exc:
            self.last_error = str(exc)
            logger.warning("ASR(%s) 调用失败, 退回 Mock: %s", self.primary.name, exc)
            return await self.fallback.transcribe(audio, filename)


class ResilientTTS(TTSProvider):
    def __init__(self, primary: TTSProvider) -> None:
        self.primary = primary
        self.fallback = MockTTS()
        self.name = primary.name
        self.last_error = ""

    async def synthesize(self, text: str) -> str:
        try:
            return await self.primary.synthesize(text)
        except Exception as exc:
            self.last_error = str(exc)
            logger.warning("TTS(%s) 调用失败, 退回 Mock: %s", self.primary.name, exc)
            return await self.fallback.synthesize(text)


@lru_cache
def get_llm() -> LLMProvider:
    provider = settings.llm_provider.lower()
    if provider == "mock" or not settings.llm_api_key:
        if provider != "mock":
            logger.warning("LLM_PROVIDER=%s 但未配置密钥, 使用 Mock 实现", provider)
        return MockLLM()
    if provider == "dashscope":
        from app.ai.dashscope import DashScopeLLM

        return ResilientLLM(DashScopeLLM())
    from app.ai.openai_compat import OpenAICompatLLM

    return ResilientLLM(OpenAICompatLLM())


def _xfyun_ready() -> bool:
    return bool(settings.xfyun_app_id and settings.xfyun_api_key and settings.xfyun_api_secret)


@lru_cache
def get_asr() -> ASRProvider:
    provider = settings.asr_provider.lower()
    if provider == "xfyun":
        if not _xfyun_ready():
            logger.warning("ASR_PROVIDER=xfyun 但讯飞凭据不完整, 使用 Mock 实现")
            return MockASR()
        from app.ai.xfyun import XfyunASR

        return ResilientASR(XfyunASR())
    key = settings.asr_api_key or settings.llm_api_key
    if provider == "mock" or not key:
        return MockASR()
    if provider == "dashscope":
        from app.ai.dashscope import DashScopeASR

        return ResilientASR(DashScopeASR())
    from app.ai.openai_compat import OpenAICompatASR

    return ResilientASR(OpenAICompatASR())


def streaming_asr_available() -> bool:
    """只有讯飞听写支持边说边出字，机器狗的"一直在听"依赖它。"""
    return settings.asr_provider.lower() == "xfyun" and _xfyun_ready()


@lru_cache
def get_tts() -> TTSProvider:
    provider = settings.tts_provider.lower()
    if provider == "xfyun":
        if not _xfyun_ready():
            logger.warning("TTS_PROVIDER=xfyun 但讯飞凭据不完整, 使用 Mock 实现")
            return MockTTS()
        from app.ai.xfyun import XfyunTTS

        return ResilientTTS(XfyunTTS())
    key = settings.tts_api_key or settings.llm_api_key
    if provider == "mock" or not key:
        return MockTTS()
    if provider == "dashscope":
        from app.ai.dashscope import DashScopeTTS

        return ResilientTTS(DashScopeTTS())
    from app.ai.openai_compat import OpenAICompatTTS

    return ResilientTTS(OpenAICompatTTS())


def ai_status() -> dict:
    llm, asr, tts = get_llm(), get_asr(), get_tts()
    return {
        "llm": {
            "provider": llm.name,
            "model": settings.llm_model if llm.name != "mock" else "本地规则引擎",
            "fallback_count": getattr(llm, "fallback_count", 0),
            "last_error": getattr(llm, "last_error", ""),
        },
        "asr": {
            "provider": asr.name,
            "streaming": streaming_asr_available(),
            "last_error": getattr(asr, "last_error", ""),
        },
        "tts": {
            "provider": tts.name,
            "voice": settings.xfyun_tts_vcn if tts.name == "xfyun" else settings.tts_voice,
            "last_error": getattr(tts, "last_error", ""),
        },
    }
