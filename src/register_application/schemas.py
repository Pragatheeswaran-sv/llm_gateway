from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field, field_validator


T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    msg: str
    status: int
    data: T


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class RegisterData(BaseModel):
    client_id: str
    client_secret_key: str



class TokenRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=255)
    client_secret_key: str = Field(min_length=1, max_length=1000, repr=False)

    @field_validator("client_id", "client_secret_key")
    @classmethod
    def strip_required_fields(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value


class TokenData(BaseModel):
    access_token: str
    time_expires: datetime
