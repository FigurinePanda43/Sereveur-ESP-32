import os
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import AccessLog, Device
from schemas import AccessModeUpdate, DeviceCreate, DeviceResponse, DeviceUpdate
from services import caddy, cloudflare, monitor

router = APIRouter(prefix="/api/devices", tags=["devices"])

DOMAIN = os.getenv("DOMAIN", "mondomaine.com")

DURATION_MAP = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "48h": timedelta(hours=48),
    "7d": timedelta(days=7),
}


@router.get("/", response_model=List[DeviceResponse])
def list_devices(db: Session = Depends(get_db)):
    return db.query(Device).order_by(Device.created_at.desc()).all()


@router.post("/", response_model=DeviceResponse, status_code=201)
async def create_device(payload: DeviceCreate, db: Session = Depends(get_db)):
    if db.query(Device).filter(Device.slug == payload.slug).first():
        raise HTTPException(status_code=409, detail="Ce slug est déjà utilisé")

    device = Device(
        project_name=payload.project_name,
        slug=payload.slug,
        local_ip=payload.local_ip,
        local_port=payload.local_port,
        description=payload.description,
        public_url=f"https://{payload.slug}.{DOMAIN}",
        status="unknown",
        access_mode="protected",
    )
    db.add(device)
    db.commit()
    db.refresh(device)

    await cloudflare.create_dns_record(payload.slug)
    await caddy.sync_caddy(db.query(Device).all())

    return device


@router.get("/{device_id}", response_model=DeviceResponse)
def get_device(device_id: int, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Équipement introuvable")
    return device


@router.put("/{device_id}", response_model=DeviceResponse)
async def update_device(device_id: int, payload: DeviceUpdate, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Équipement introuvable")

    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(device, field, value)
    db.commit()
    db.refresh(device)

    await caddy.sync_caddy(db.query(Device).all())
    return device


@router.delete("/{device_id}", status_code=204)
async def delete_device(device_id: int, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Équipement introuvable")

    slug = device.slug
    db.delete(device)
    db.commit()

    await cloudflare.delete_dns_record(slug)
    await caddy.sync_caddy(db.query(Device).all())


@router.post("/{device_id}/refresh", response_model=DeviceResponse)
async def refresh_device(device_id: int, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Équipement introuvable")

    await monitor.check_device(db, device)
    db.refresh(device)
    return device


@router.post("/{device_id}/access-mode", response_model=DeviceResponse)
async def set_access_mode(device_id: int, payload: AccessModeUpdate, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Équipement introuvable")

    now = datetime.now(timezone.utc)
    old_mode = device.access_mode

    device.access_mode = payload.access_mode
    device.last_access_mode_change = now

    if payload.access_mode == "public_temporary":
        delta = DURATION_MAP[payload.duration]
        device.public_until = now + delta
        log_type = "public_access_started"
    else:
        device.public_until = None
        if old_mode == "public_temporary":
            log_type = "public_access_closed_manually"
        else:
            log_type = "access_mode_changed"

    log = AccessLog(
        device_id=device.id,
        event_type=log_type,
        old_mode=old_mode,
        new_mode=payload.access_mode,
        public_until=device.public_until,
        message=f"Mode changé : {old_mode} → {payload.access_mode}",
    )
    db.add(log)
    db.commit()
    db.refresh(device)

    synced = await caddy.sync_caddy(db.query(Device).all())
    if not synced:
        raise HTTPException(status_code=500, detail="Synchronisation Caddy impossible")

    return device
