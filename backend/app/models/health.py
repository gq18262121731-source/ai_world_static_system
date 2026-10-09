from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class HealthRecord(Base):
    """手表上报的健康遥测点。"""

    __tablename__ = "health_record"
    __table_args__ = (
        Index("ix_health_record_elder_time", "elder_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    elder_id: Mapped[int] = mapped_column(
        ForeignKey("elder.id", ondelete="CASCADE"), index=True
    )
    device_id: Mapped[str] = mapped_column(String(64), default="")
    recorded_at: Mapped[datetime] = mapped_column(DateTime, index=True)

    heart_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    systolic: Mapped[int | None] = mapped_column(Integer, nullable=True)
    diastolic: Mapped[int | None] = mapped_column(Integer, nullable=True)
    spo2: Mapped[int | None] = mapped_column(Integer, nullable=True)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    steps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleep_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 静息 / 轻度 / 中度活动
    activity_level: Mapped[str] = mapped_column(String(16), default="rest")
    battery: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="watch")
    # 被判定为孤立坏读数的字段名，逗号分隔（如 "heart_rate,spo2"）。
    # 原值照常保存以便追溯，但评估、告警、统计一律跳过这些字段。
    suspicious_fields: Mapped[str] = mapped_column(
        String(96), default="", server_default="", nullable=False
    )

    def suspicious(self) -> set[str]:
        return {item for item in (self.suspicious_fields or "").split(",") if item}

    def trusted(self, key: str):
        """取字段值；被标记为可疑的字段视为没有读数。"""
        value = getattr(self, key, None)
        if value is None or key in self.suspicious():
            return None
        return value


class HealthDailySummary(Base, TimestampMixin):
    """按天聚合的健康摘要, 用于趋势分析与外出评估。"""

    __tablename__ = "health_daily_summary"
    __table_args__ = (
        Index("uq_health_daily_elder_date", "elder_id", "summary_date", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    elder_id: Mapped[int] = mapped_column(
        ForeignKey("elder.id", ondelete="CASCADE"), index=True
    )
    summary_date: Mapped[date] = mapped_column(Date, index=True)

    hr_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    hr_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hr_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    spo2_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    spo2_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    systolic_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    diastolic_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    steps_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleep_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    anomaly_count: Mapped[int] = mapped_column(Integer, default=0)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    # 当日健康评分（0~100，越高越好）与等级，每次上报后刷新，用于画评分趋势
    health_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    health_grade: Mapped[str | None] = mapped_column(String(4), nullable=True)
    health_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 当日静息心率（可信心率的第 10 百分位）与血氧中位数：个人基线由近 14 天的这两列算出
    resting_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    spo2_median: Mapped[float | None] = mapped_column(Float, nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
