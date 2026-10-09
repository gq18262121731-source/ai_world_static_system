from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class EmergencyContactBase(BaseModel):
    name: str
    phone: str
    relation: str = "子女"
    priority: int = 1


class EmergencyContactOut(EmergencyContactBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class ElderBase(BaseModel):
    name: str
    gender: str = "男"
    birth_date: date | None = None
    phone: str = ""
    address: str = ""
    city: str = "杭州"
    home_lat: float = 30.2741
    home_lng: float = 120.1551
    chronic_diseases: list[str] = Field(default_factory=list)
    mobility_level: str = "good"
    live_alone: bool = True
    note: str = ""


class ElderCreate(ElderBase):
    emergency_contacts: list[EmergencyContactBase] = Field(default_factory=list)


class ElderUpdate(BaseModel):
    name: str | None = None
    gender: str | None = None
    birth_date: date | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    home_lat: float | None = None
    home_lng: float | None = None
    chronic_diseases: list[str] | None = None
    mobility_level: str | None = None
    live_alone: bool | None = None
    note: str | None = None


class ElderOut(ElderBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    age: int | None = None
    emergency_contacts: list[EmergencyContactOut] = Field(default_factory=list)
