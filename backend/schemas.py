import ipaddress
import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

RESERVED_SLUGS = {"iot", "api", "www", "mail", "ftp", "admin", "root", "docs"}
SLUG_PATTERN = re.compile(r"^[a-z0-9-]+$")

VALID_DURATIONS = {"15m", "1h", "24h", "48h", "7d"}


class DeviceCreate(BaseModel):
    project_name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=1, max_length=63)
    local_ip: str
    local_port: int = Field(80, ge=1, le=65535)
    local_protocol: str = Field("http", pattern="^(http|https)$")
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
    local_protocol: Optional[str] = Field(None, pattern="^(http|https)$")
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
    local_protocol: str
    description: str
    access_mode: str
    public_until: Optional[datetime]
    last_access_mode_change: Optional[datetime]
    status: str
    status_detail: Optional[str]
    created_at: datetime
    last_seen: Optional[datetime]

    model_config = {"from_attributes": True}


class AccessModeUpdate(BaseModel):
    access_mode: str
    duration: Optional[str] = None

    @field_validator("access_mode")
    @classmethod
    def validate_access_mode(cls, v: str) -> str:
        valid = {"suspended", "protected", "public_temporary", "public"}
        if v not in valid:
            raise ValueError(f"Mode d'accès invalide. Valeurs autorisées : {', '.join(valid)}")
        return v

    @field_validator("duration")
    @classmethod
    def validate_duration(cls, v: Optional[str], info) -> Optional[str]:
        if v is not None and v not in VALID_DURATIONS:
            raise ValueError(f"Durée invalide. Valeurs autorisées : {', '.join(VALID_DURATIONS)}")
        return v

    def model_post_init(self, __context) -> None:
        if self.access_mode == "public_temporary" and not self.duration:
            raise ValueError("La durée est requise pour le mode public_temporary")
        if self.access_mode == "public" and self.duration:
            raise ValueError("Le mode public permanent n'accepte pas de durée")


class LoginRequest(BaseModel):
    username: str
    password: str
