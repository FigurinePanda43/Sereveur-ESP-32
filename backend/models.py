from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    country = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class BlockedIP(Base):
    """Bannissement d'une IP (automatique par l'anti-force brute ou manuel).

    La ligne est conservée après expiration : ``ban_count`` sert au
    bannissement progressif des récidivistes et ``updated_at`` marque le début
    du dernier bannissement (les échecs antérieurs ne sont plus recomptés).
    """
    __tablename__ = "blocked_ips"

    id = Column(Integer, primary_key=True)
    ip_address = Column(String, unique=True, nullable=False, index=True)
    blocked_until = Column(DateTime(timezone=True), nullable=False)
    reason = Column(String, default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    # Colonnes IPBan (ajoutées par migration dans main.py)
    permanent = Column(Boolean, nullable=False, default=False)
    source = Column(String, nullable=False, default="auto")  # auto | manual
    ban_count = Column(Integer, nullable=False, default=1)
    country = Column(String, nullable=True)


class SecuritySettings(Base):
    """Réglages IPBan — une seule ligne (id = 1), créée à la première lecture."""
    __tablename__ = "security_settings"

    id = Column(Integer, primary_key=True)
    enabled = Column(Boolean, nullable=False, default=True)
    # standard : tout le monde passe sauf les IP bannies / en liste noire.
    # whitelist : seules les IP de la liste blanche passent.
    mode = Column(String, nullable=False, default="standard")
    apply_to_services = Column(Boolean, nullable=False, default=True)
    exempt_private = Column(Boolean, nullable=False, default=True)
    # off | allow (seuls ces pays) | block (tous sauf ces pays)
    country_mode = Column(String, nullable=False, default="off")
    countries = Column(String, nullable=False, default="")  # codes ISO séparés par des virgules
    bruteforce_enabled = Column(Boolean, nullable=False, default=True)
    rules = Column(Text, nullable=False, default="[]")  # JSON : [{window_minutes, max_failures, ban_minutes}]
    escalation_factor = Column(Float, nullable=False, default=2.0)
    max_ban_minutes = Column(Integer, nullable=False, default=30 * 24 * 60)
    permanent_after = Column(Integer, nullable=False, default=0)  # 0 = jamais
    retention_days = Column(Integer, nullable=False, default=30)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IPRule(Base):
    """Entrée de liste blanche (``allow``) ou noire (``deny``) : IP ou plage CIDR."""
    __tablename__ = "ip_rules"
    __table_args__ = (UniqueConstraint("list_type", "value", name="uq_ip_rule"),)

    id = Column(Integer, primary_key=True)
    list_type = Column(String, nullable=False, index=True)
    value = Column(String, nullable=False)
    note = Column(String, default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SecurityEvent(Base):
    """Journal IPBan. Les requêtes bloquées sont agrégées (``count``) pour
    qu'une rafale ne produise pas une ligne par requête."""
    __tablename__ = "security_events"

    id = Column(Integer, primary_key=True)
    event_type = Column(String, nullable=False, index=True)  # blocked | ban | manual_ban | unban | settings | list
    ip_address = Column(String, nullable=True, index=True)
    country = Column(String, nullable=True)
    host = Column(String, nullable=True)
    path = Column(String, nullable=True)
    reason = Column(String, nullable=True)
    message = Column(String, nullable=True)
    count = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    last_seen = Column(DateTime(timezone=True), nullable=True)


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
