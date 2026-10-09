from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AlarmNotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel: str
    target: str
    target_name: str
    content: str
    status: str
    sent_at: datetime


class AlarmOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    elder_id: int
    alarm_type: str
    level: str
    status: str
    title: str
    description: str
    triggered_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    handled_by: str
    handle_note: str
    lat: float | None
    lng: float | None
    task_id: int | None
    escalation_level: int
    data: dict = Field(default_factory=dict)


class AlarmDetailOut(AlarmOut):
    elder_name: str = ""
    notifications: list[AlarmNotificationOut] = Field(default_factory=list)


class AlarmHandleRequest(BaseModel):
    status: str  # acked / resolved / false_alarm
    note: str = ""


class FallEventIn(BaseModel):
    elder_id: int | None = None
    device_id: str = ""
    source: str = "watch"
    confidence: float = 0.9
    lat: float | None = None
    lng: float | None = None
    detected_at: datetime | None = None
    raw: dict = Field(default_factory=dict)
