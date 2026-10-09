from pydantic import BaseModel, ConfigDict, Field


class RuleConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    elder_id: int | None
    rule_key: str
    value: dict = Field(default_factory=dict)
    enabled: bool
    description: str


class RuleConfigUpsert(BaseModel):
    rule_key: str
    value: dict = Field(default_factory=dict)
    enabled: bool = True
    description: str = ""


class EffectiveRules(BaseModel):
    elder_id: int | None = None
    values: dict = Field(default_factory=dict)
    overridden_keys: list[str] = Field(default_factory=list)
