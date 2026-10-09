from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class ChatMessage:
    role: str  # system / user / assistant
    content: str

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


@dataclass
class ChatRequest:
    messages: list[ChatMessage]
    temperature: float = 0.3
    json_mode: bool = False
    max_tokens: int = 800
    # task / context 仅供 Mock 实现使用, 真实模型忽略
    task: str = ""
    context: dict = field(default_factory=dict)


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def chat(self, request: ChatRequest) -> str:
        """返回模型生成的纯文本内容。"""

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[str]:
        """逐段产出文本。不支持流式的实现退化为一次性产出完整内容。"""
        yield await self.chat(request)

    @property
    def available(self) -> bool:
        return True


class ASRProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def transcribe(self, audio: bytes, filename: str = "audio.wav") -> str:
        """音频 -> 文本。"""


class TTSProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def synthesize(self, text: str) -> str:
        """文本 -> 音频, 返回可访问的音频 URL(空串表示由前端本地合成)。"""
