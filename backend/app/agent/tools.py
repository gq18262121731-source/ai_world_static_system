"""智能体可调用的工具集。

每个工具都是对平台真实数据/能力的封装, 调用记录会写进对话留痕,
后台可以看到某一次回答究竟依据了哪些数据。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Elder
from app.models.enums import AlarmLevel, AlarmType
from app.schemas.health import HealthAssessment
from app.services import (
    alarm_service,
    device_service,
    evaluation_service,
    health_service,
    outing_service,
    weather_service,
)

logger = get_logger(__name__)


class ToolBox:
    def __init__(self, db: AsyncSession, elder: Elder) -> None:
        self.db = db
        self.elder = elder
        self.used: list[str] = []

    def _record(self, name: str) -> None:
        if name not in self.used:
            self.used.append(name)

    async def get_elder_profile(self) -> dict:
        self._record("get_elder_profile")
        return {
            "name": self.elder.name,
            "age": self.elder.age,
            "gender": self.elder.gender,
            "chronic_diseases": self.elder.chronic_diseases or [],
            "mobility_level": self.elder.mobility_level,
            "city": self.elder.city,
        }

    async def get_recent_health(self, window_hours: int = 24) -> HealthAssessment:
        self._record("get_recent_health")
        return await health_service.build_assessment(self.db, self.elder, window_hours)

    async def get_outing_stats(self) -> dict:
        self._record("get_outing_stats")
        stats = await outing_service.get_stats(self.db, self.elder.id)
        return stats.model_dump(mode="json")

    async def get_weather(self) -> dict:
        self._record("get_weather")
        return await weather_service.get_weather(
            self.elder.city, self.elder.home_lat, self.elder.home_lng
        )

    async def get_device_status(self) -> dict:
        self._record("get_device_status")
        return await device_service.device_status_snapshot(self.db, self.elder.id)

    async def evaluate_outing(self, question: str):
        self._record("evaluate_outing")
        return await evaluation_service.evaluate_and_persist(
            self.db, self.elder, question, channel="voice"
        )

    async def start_following(self, decision):
        self._record("start_following")
        return await outing_service.start_task(self.db, self.elder, decision)

    async def end_following(self, reason: str = "voice"):
        self._record("end_following")
        task = await outing_service.get_active_task(self.db, self.elder.id)
        if not task:
            return None
        return await outing_service.end_task(self.db, task, end_reason=reason)

    async def raise_alarm(
        self, alarm_type: str, title: str, description: str, level: str = AlarmLevel.CRITICAL
    ):
        self._record("raise_alarm")
        if alarm_type == AlarmType.SOS:
            return await alarm_service.trigger_sos(self.db, self.elder.id, description)
        return await alarm_service.create_alarm(
            self.db,
            elder_id=self.elder.id,
            alarm_type=alarm_type,
            level=level,
            title=title,
            description=description,
        )
