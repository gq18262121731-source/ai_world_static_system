from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import UserRole


class User(Base, TimestampMixin):
    __tablename__ = "sys_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    real_name: Mapped[str] = mapped_column(String(64), default="")
    phone: Mapped[str] = mapped_column(String(32), default="")
    role: Mapped[str] = mapped_column(String(16), default=UserRole.FAMILY)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    family_links: Mapped[list["FamilyLink"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )


class FamilyLink(Base, TimestampMixin):
    """家属账号与老人的关联关系。"""

    __tablename__ = "family_link"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("sys_user.id", ondelete="CASCADE"), index=True
    )
    elder_id: Mapped[int] = mapped_column(
        ForeignKey("elder.id", ondelete="CASCADE"), index=True
    )
    relation: Mapped[str] = mapped_column(String(32), default="子女")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship(back_populates="family_links")
    elder: Mapped["Elder"] = relationship(back_populates="family_links")  # noqa: F821
