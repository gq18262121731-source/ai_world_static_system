from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import AlarmLevel, AlarmStatus


class Alarm(Base, TimestampMixin):
    __tablename__ = "alarm"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    elder_id: Mapped[int] = mapped_column(
        ForeignKey("elder.id", ondelete="CASCADE"), index=True
    )
    alarm_type: Mapped[str] = mapped_column(String(24), index=True)
    level: Mapped[str] = mapped_column(String(16), default=AlarmLevel.WARNING, index=True)
    status: Mapped[str] = mapped_column(
        String(16), default=AlarmStatus.PENDING, index=True
    )
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    triggered_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    handled_by: Mapped[str] = mapped_column(String(64), default="")
    handle_note: Mapped[str] = mapped_column(Text, default="")
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    escalation_level: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class AlarmNotification(Base, TimestampMixin):
    __tablename__ = "alarm_notification"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alarm_id: Mapped[int] = mapped_column(
        ForeignKey("alarm.id", ondelete="CASCADE"), index=True
    )
    # sms / voice_call / app_push / dog_speak / websocket
    channel: Mapped[str] = mapped_column(String(24))
    target: Mapped[str] = mapped_column(String(128), default="")
    target_name: Mapped[str] = mapped_column(String(64), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="sent")
    sent_at: Mapped[datetime] = mapped_column(DateTime)


class FallEvent(Base, TimestampMixin):
    """跌倒原始事件, 多源融合后指向同一条告警。"""

    __tablename__ = "fall_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    elder_id: Mapped[int] = mapped_column(
        ForeignKey("elder.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(16), index=True)
    device_id: Mapped[str] = mapped_column(String(64), default="")
    detected_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    alarm_id: Mapped[int | None] = mapped_column(
        ForeignKey("alarm.id", ondelete="SET NULL"), nullable=True, index=True
    )
    merged: Mapped[bool] = mapped_column(default=False)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
