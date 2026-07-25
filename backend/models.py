from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    project_name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False, index=True)
    public_url = Column(String)
    local_ip = Column(String, nullable=False)
    local_port = Column(Integer, nullable=False, default=80)
    local_protocol = Column(String, nullable=False, default="http")
    description = Column(String, default="")
    enabled = Column(Boolean, nullable=False, default=True)  # kept for migration compatibility
    status = Column(String, default="unknown")
    status_detail = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    last_seen = Column(DateTime, nullable=True)
    # New columns (added via migration in main.py lifespan)
    access_mode = Column(String, nullable=False, default="protected")
    public_until = Column(DateTime(timezone=True), nullable=True)
    last_access_mode_change = Column(DateTime(timezone=True), nullable=True)


class User(Base):
    """Utilisateur secondaire créé par l'administrateur, avec accès limité
    à un ou plusieurs services (devices) et une date de validité optionnelle."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    # None = accès sans expiration. Stocké en UTC naïf (cohérent avec datetime.utcnow()).
    valid_until = Column(DateTime, nullable=True)
    description = Column(String, default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_login = Column(DateTime, nullable=True)

    accesses = relationship(
        "UserDeviceAccess",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class UserDeviceAccess(Base):
    """Association utilisateur ↔ service autorisé."""
    __tablename__ = "user_device_access"
    __table_args__ = (UniqueConstraint("user_id", "device_id", name="uq_user_device"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="accesses")
    device = relationship("Device")


class AuthAttempt(Base):
    __tablename__ = "auth_attempts"

    id = Column(Integer, primary_key=True)
    ip_address = Column(String, nullable=False)
    username = Column(String, nullable=False)
    user_agent = Column(String, default="")
    success = Column(Boolean, nullable=False, default=False)
    failure_reason = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class BlockedIP(Base):
    __tablename__ = "blocked_ips"

    id = Column(Integer, primary_key=True)
    ip_address = Column(String, unique=True, nullable=False, index=True)
    blocked_until = Column(DateTime(timezone=True), nullable=False)
    reason = Column(String, default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class AccessLog(Base):
    __tablename__ = "access_logs"

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, nullable=True)
    event_type = Column(String, nullable=False)
    old_mode = Column(String, nullable=True)
    new_mode = Column(String, nullable=True)
    public_until = Column(DateTime(timezone=True), nullable=True)
    source_ip = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    message = Column(String, nullable=True)
