from datetime import date

from sqlalchemy import JSON, Date, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Elder(Base, TimestampMixin):
    __tablename__ = "elder"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    gender: Mapped[str] = mapped_column(String(8), default="男")
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    phone: Mapped[str] = mapped_column(String(32), default="")
    address: Mapped[str] = mapped_column(String(255), default="")
    city: Mapped[str] = mapped_column(String(64), default="杭州")
    home_lat: Mapped[float] = mapped_column(Float, default=30.2741)
    home_lng: Mapped[float] = mapped_column(Float, default=120.1551)
    # 慢性病列表, 例: ["高血压", "2型糖尿病"]
    chronic_diseases: Mapped[list] = mapped_column(JSON, default=list)
    # 行动能力: good / limited / poor
    mobility_level: Mapped[str] = mapped_column(String(16), default="good")
    live_alone: Mapped[bool] = mapped_column(default=True)
    note: Mapped[str] = mapped_column(Text, default="")

    family_links: Mapped[list["FamilyLink"]] = relationship(  # noqa: F821
        back_populates="elder", cascade="all, delete-orphan", lazy="selectin"
    )
    emergency_contacts: Mapped[list["EmergencyContact"]] = relationship(
        back_populates="elder", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def age(self) -> int | None:
        if not self.birth_date:
            return None
        today = date.today()
        return (
            today.year
            - self.birth_date.year
            - ((today.month, today.day) < (self.birth_date.month, self.birth_date.day))
        )


class EmergencyContact(Base, TimestampMixin):
    __tablename__ = "emergency_contact"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    elder_id: Mapped[int] = mapped_column(
        ForeignKey("elder.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64))
    phone: Mapped[str] = mapped_column(String(32))
    relation: Mapped[str] = mapped_column(String(32), default="子女")
    # 数字越小越先通知
    priority: Mapped[int] = mapped_column(Integer, default=1)

    elder: Mapped["Elder"] = relationship(back_populates="emergency_contacts")
