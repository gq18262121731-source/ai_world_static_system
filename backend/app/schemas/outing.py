from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RuleHit(BaseModel):
    key: str
    level: str            # block(硬性红线) / warn(软性风险) / info
    title: str
    detail: str
    value: str = ""
    threshold: str = ""


class RuleReport(BaseModel):
    elder_id: int
    blocked: bool = False
    hits: list[RuleHit] = Field(default_factory=list)
    passed_rules: list[str] = Field(default_factory=list)
    suggested_duration_minutes: int = 40
    suggested_radius_m: int = 800
    risk_score: float = 0.0
    evaluated_at: datetime | None = None
    # 不影响结论、但值得告诉老人的事：带伞、天黑前回家、带糖……
    advisories: list[str] = Field(default_factory=list)
    # 时长被预报里的降水压短时，说明为什么
    duration_cap_reason: str = ""
    # 现在不合适时，从预报里挑出的下一个合适时段，如 "10:00"
    best_time_hint: str = ""
    sunset: str = ""

    @property
    def block_hits(self) -> list[RuleHit]:
        return [h for h in self.hits if h.level == "block"]

    @property
    def warn_hits(self) -> list[RuleHit]:
        return [h for h in self.hits if h.level == "warn"]


class OutingLimits(BaseModel):
    duration_minutes: int = 30
    radius_meters: int = 500
    need_follow: bool = True
    return_before: str = ""


class OutingEvaluation(BaseModel):
    decision: str                                  # allow / conditional / deny
    reasons: list[str] = Field(default_factory=list)
    advice: list[str] = Field(default_factory=list)
    limits: OutingLimits = Field(default_factory=OutingLimits)
    speech_text: str = ""
    confidence: float = 0.8
    overridden_by_rules: bool = False
    llm_raw: str = ""
    llm_provider: str = "mock"
    # 智能体从老人的话里理解出的外出目的与期望时长
    purpose: str = ""
    expected_minutes: int | None = None
    # 出门前要带的东西 / 路线提示 / 不合适时下一个合适时段 / 一句关心的话
    preparation: list[str] = Field(default_factory=list)
    route_hint: str = ""
    best_time_hint: str = ""
    care_note: str = ""

    def extras(self) -> dict:
        return {
            "purpose": self.purpose,
            "expected_minutes": self.expected_minutes,
            "preparation": self.preparation,
            "route_hint": self.route_hint,
            "best_time_hint": self.best_time_hint,
            "care_note": self.care_note,
        }


class OutingDecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_id: int
    elder_id: int
    decision: str
    reasons: list = Field(default_factory=list)
    advice: list = Field(default_factory=list)
    limits: dict = Field(default_factory=dict)
    rule_report: dict = Field(default_factory=dict)
    health_snapshot: dict = Field(default_factory=dict)
    weather_snapshot: dict = Field(default_factory=dict)
    outing_stats: dict = Field(default_factory=dict)
    llm_provider: str = "mock"
    llm_raw: str = ""
    overridden_by_rules: bool = False
    confidence: float = 0.0
    speech_text: str = ""
    extras: dict = Field(default_factory=dict)
    created_at: datetime


class OutingRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    elder_id: int
    device_id: str
    channel: str
    raw_text: str
    requested_at: datetime
    decision: OutingDecisionOut | None = None


class OutingTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    elder_id: int
    decision_id: int | None
    dog_device_id: str
    status: str
    started_at: datetime
    ended_at: datetime | None
    planned_duration_minutes: int
    geofence_radius_m: int
    origin_lat: float
    origin_lng: float
    max_distance_m: float
    total_distance_m: float
    follow_active: bool
    summary: str
    end_reason: str


class TrackPointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ts: datetime
    lat: float
    lng: float
    speed: float
    distance_from_origin_m: float


class OutingStats(BaseModel):
    today_count: int = 0
    week_count: int = 0
    month_count: int = 0
    last_outing_at: datetime | None = None
    minutes_since_last_return: int | None = None
    today_total_minutes: int = 0
    active_task_id: int | None = None
    avg_duration_minutes: float = 0.0


class ManualOutingRequest(BaseModel):
    elder_id: int
    text: str = "我想出去走走"
    channel: str = "manual"
