from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DeviceCreate(BaseModel):
    device_id: str
    device_type: str
    name: str = ""
    elder_id: int | None = None


class DeviceUpdate(BaseModel):
    name: str | None = None
    elder_id: int | None = None


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: str
    device_type: str
    name: str
    elder_id: int | None
    status: str
    battery: int
    firmware: str
    last_seen_at: datetime | None
    last_lat: float | None
    last_lng: float | None
    extra: dict = Field(default_factory=dict)


class DeviceCommand(BaseModel):
    """下发给机器狗的指令。"""

    command: str
    payload: dict = Field(default_factory=dict)
