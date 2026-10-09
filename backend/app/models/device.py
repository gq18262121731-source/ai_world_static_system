from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import DeviceStatus


class Device(Base, TimestampMixin):
    __tablename__ = "device"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_type: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    elder_id: Mapped[int | None] = mapped_column(
        ForeignKey("elder.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default=DeviceStatus.OFFLINE)
    battery: Mapped[int] = mapped_column(Integer, default=100)
    firmware: Mapped[str] = mapped_column(String(32), default="1.0.0")
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    last_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 机器狗: {"follow_mode": false, "gait": "idle"}
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
