from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PromptTemplate(Base, TimestampMixin):
    """可在后台编辑的提示词与机器狗话术。

    未配置或被禁用时回落到代码中的默认值(app/agent/templates.py),
    保证即使模板被改坏, 智能体依然能正常工作。
    """

    __tablename__ = "prompt_template"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    # system_prompt(提示词) / speech(机器狗话术)
    category: Mapped[str] = mapped_column(String(24), default="speech", index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String(64), default="")
