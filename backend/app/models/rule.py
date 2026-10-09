from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class RuleConfig(Base, TimestampMixin):
    """外出评估阈值配置。elder_id 为空表示全局默认值, 非空为个性化覆盖。"""

    __tablename__ = "rule_config"
    __table_args__ = (
        Index("uq_rule_config_scope", "elder_id", "rule_key", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    elder_id: Mapped[int | None] = mapped_column(
        ForeignKey("elder.id", ondelete="CASCADE"), nullable=True, index=True
    )
    rule_key: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(Text, default="")
