from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
MASK = "••••••••"


def validate_key(value: str) -> str:
    value = value.strip()
    if not KEY_RE.match(value):
        raise ValueError("Key must start with a letter or underscore and contain only letters, digits and _")
    return value


class EnvVarCreate(BaseModel):
    key: str = Field(max_length=128)
    value: str = Field(max_length=32_768)
    is_secret: bool = True

    _key = field_validator("key")(validate_key)

    @field_validator("value")
    @classmethod
    def _no_nul(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Value cannot contain NUL bytes")
        return value


class EnvVarUpdate(BaseModel):
    value: str | None = Field(default=None, max_length=32_768)
    is_secret: bool | None = None

    @field_validator("value")
    @classmethod
    def _no_nul(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("Value cannot contain NUL bytes")
        return value


class EnvVarOut(BaseModel):
    key: str
    value: str = Field(description="Plain value for non-secret variables, a mask for secrets")
    is_secret: bool
    created_at: datetime
    updated_at: datetime


class EnvVarRevealed(BaseModel):
    key: str
    value: str
