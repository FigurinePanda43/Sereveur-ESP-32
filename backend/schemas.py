import ipaddress
import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

RESERVED_SLUGS = {"iot", "api", "www", "mail", "ftp", "admin", "root", "docs"}
SLUG_PATTERN = re.compile(r"^[a-z0-9-]+$")


class DeviceCreate(BaseModel):
    project_name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=1, max_length=63)
    local_ip: str
    local_port: int = Field(80, ge=1, le=65535)
    description: str = Field("", max_length=500)

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        if not SLUG_PATTERN.match(v):
            raise ValueError("Le slug ne peut contenir que des lettres minuscules, chiffres et tirets")
        if v in RESERVED_SLUGS:
            raise ValueError(f"Le slug '{v}' est réservé")
        return v

    @field_validator("local_ip")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        try:
            ipaddress.ip_address(v)
        except ValueError:
            raise ValueError(f"Adresse IP invalide : {v}")
        return v


class DeviceUpdate(BaseModel):
    project_name: Optional[str] = Field(None, min_length=1, max_length=100)
    local_ip: Optional[str] = None
    local_port: Optional[int] = Field(None, ge=1, le=65535)
    description: Optional[str] = Field(None, max_length=500)

    @field_validator("local_ip")
    @classmethod
    def validate_ip(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            ipaddress.ip_address(v)
        except ValueError:
            raise ValueError(f"Adresse IP invalide : {v}")
        return v


class DeviceResponse(BaseModel):
    id: int
    project_name: str
    slug: str
    public_url: Optional[str]
    local_ip: str
    local_port: int
    description: str
    enabled: bool
    status: str
    created_at: datetime
    last_seen: Optional[datetime]

    model_config = {"from_attributes": True}
