from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import OutingTaskStatus


class OutingRequest(Base, TimestampMixin):
    """老人发起的一次外出申请(语音或手动)。"""

    __tablename__ = "outing_request"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    elder_id: Mapped[int] = mapped_column(
        ForeignKey("elder.id", ondelete="CASCADE"), index=True
    )
    device_id: Mapped[str] = mapped_column(String(64), default="")
    channel: Mapped[str] = mapped_column(String(16), default="voice")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    requested_at: Mapped[datetime] = mapped_column(DateTime, index=True)

    decision: Mapped["OutingDecision | None"] = relationship(
        back_populates="request",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class OutingDecision(Base, TimestampMixin):
    """一次外出评估的完整留痕: 规则报告 + 健康快照 + 模型原始输出。"""

    __tablename__ = "outing_decision"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("outing_request.id", ondelete="CASCADE"), index=True
    )
    elder_id: Mapped[int] = mapped_column(
        ForeignKey("elder.id", ondelete="CASCADE"), index=True
    )
    decision: Mapped[str] = mapped_column(String(16), index=True)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    advice: Mapped[list] = mapped_column(JSON, default=list)
    limits: Mapped[dict] = mapped_column(JSON, default=dict)
    rule_report: Mapped[dict] = mapped_column(JSON, default=dict)
    health_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    weather_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    outing_stats: Mapped[dict] = mapped_column(JSON, default=dict)
    llm_raw: Mapped[str] = mapped_column(Text, default="")
    llm_provider: Mapped[str] = mapped_column(String(32), default="mock")
    # 模型结论被规则层否决时置 true, 用于后台复核
    overridden_by_rules: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    speech_text: Mapped[str] = mapped_column(Text, default="")
    engine_version: Mapped[str] = mapped_column(String(16), default="v1")
    # 智能体的补充输出：外出目的、准备清单、路线提示、最佳时段、关心的话
    extras: Mapped[dict] = mapped_column(JSON, default=dict, nullable=True)

    request: Mapped["OutingRequest"] = relationship(back_populates="decision")


class OutingTask(Base, TimestampMixin):
    """评估通过后创建的外出任务, 机器狗进入跟随模式。"""

    __tablename__ = "outing_task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    elder_id: Mapped[int] = mapped_column(
        ForeignKey("elder.id", ondelete="CASCADE"), index=True
    )
    decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("outing_decision.id", ondelete="SET NULL"), nullable=True
    )
    dog_device_id: Mapped[str] = mapped_column(String(64), default="")
    watch_device_id: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(
        String(16), default=OutingTaskStatus.ACTIVE, index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    planned_duration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    geofence_radius_m: Mapped[int] = mapped_column(Integer, default=500)
    origin_lat: Mapped[float] = mapped_column(Float, default=0.0)
    origin_lng: Mapped[float] = mapped_column(Float, default=0.0)
    max_distance_m: Mapped[float] = mapped_column(Float, default=0.0)
    total_distance_m: Mapped[float] = mapped_column(Float, default=0.0)
    follow_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # 已触发过的提醒类型, 避免重复告警
    fired_alerts: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    end_reason: Mapped[str] = mapped_column(String(32), default="")


class TrackPoint(Base):
    __tablename__ = "track_point"
    __table_args__ = (Index("ix_track_point_task_ts", "task_id", "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("outing_task.id", ondelete="CASCADE"), index=True
    )
    elder_id: Mapped[int] = mapped_column(Integer, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    speed: Mapped[float] = mapped_column(Float, default=0.0)
    distance_from_origin_m: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(16), default="dog")
