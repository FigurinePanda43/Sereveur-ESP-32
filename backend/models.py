from sqlalchemy import Column, Integer, String, DateTime
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
    description = Column(String, default="")
    status = Column(String, default="unknown")
    created_at = Column(DateTime, server_default=func.now())
    last_seen = Column(DateTime, nullable=True)
