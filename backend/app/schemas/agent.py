from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class VoiceTextIn(BaseModel):
    """机器狗上行的语音识别结果(或前端模拟输入)。"""

    device_id: str = ""
    elder_id: int | None = None
    text: str
    conversation_id: int | None = None


class AgentAction(BaseModel):
    """智能体决定要下发给设备的动作。"""

    type: str                             # speak / start_follow / stop_follow / return_home / alarm
    payload: dict = Field(default_factory=dict)


class AgentReply(BaseModel):
    conversation_id: int
    elder_id: int
    intent: str
    request_text: str
    reply_text: str
    audio_url: str = ""
    actions: list[AgentAction] = Field(default_factory=list)
    decision: dict | None = None
    task_id: int | None = None
    tools_used: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    llm_provider: str = "mock"
    # 各环节耗时（意图/推理/合成），前端把链路逐个点亮时用
    stages: dict = Field(default_factory=dict)
    # dog = 机器狗语音通道；console = 值守人员控制台问答
    channel: str = "dog"


class ConsoleChatIn(BaseModel):
    """控制台问答：值守人员打字问某位老人的情况。"""

    elder_id: int
    question: str
    conversation_id: int | None = None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    intent: str
    text: str
    audio_url: str
    latency_ms: int
    meta: dict = Field(default_factory=dict)
    created_at: datetime


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    elder_id: int
    device_id: str
    started_at: datetime
    last_active_at: datetime
    status: str
    messages: list[MessageOut] = Field(default_factory=list)


class AgentTurn(BaseModel):
    """一轮完整对话: 老人一句话 + 机器狗一句回复, 附带推理过程。"""

    message_id: int
    conversation_id: int
    elder_id: int | None = None
    elder_name: str = ""
    device_id: str = ""
    intent: str = ""
    intent_confidence: float | None = None
    question: str = ""
    answer: str = ""
    audio_url: str = ""
    tools: list[str] = Field(default_factory=list)
    stages: dict[str, int] = Field(default_factory=dict)
    llm_provider: str = ""
    decision: dict | None = None
    task_id: int | None = None
    latency_ms: int = 0
    created_at: datetime


class AgentTurnPage(BaseModel):
    items: list[AgentTurn]
    total: int
    page: int = 1
    page_size: int = 20


class AgentMetrics(BaseModel):
    total_turns: int = 0
    avg_latency_ms: float = 0.0
    max_latency_ms: int = 0
    intent_distribution: dict[str, int] = Field(default_factory=dict)


class PromptTemplateOut(BaseModel):
    template_key: str
    name: str
    category: str
    description: str = ""
    placeholders: list[str] = Field(default_factory=list)
    content: str
    default_content: str
    overridden: bool = False
    enabled: bool = True
    updated_by: str = ""
    updated_at: datetime | None = None


class PromptTemplateUpdate(BaseModel):
    content: str
    enabled: bool = True


class ProviderTestResult(BaseModel):
    capability: str          # llm / tts
    provider: str
    ok: bool
    latency_ms: int = 0
    detail: str = ""
    sample: str = ""
