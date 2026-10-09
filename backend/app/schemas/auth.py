from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    real_name: str
    phone: str
    role: str
    is_active: bool
    elder_ids: list[int] = Field(default_factory=list)


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=64)
    real_name: str = ""
    phone: str = ""
    role: str = "family"
    elder_ids: list[int] = Field(default_factory=list)
    relation: str = "子女"


TokenResponse.model_rebuild()
