import os
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Device
from schemas import DeviceCreate, DeviceResponse, DeviceUpdate
from services import caddy, cloudflare, monitor

router = APIRouter(prefix="/api/devices", tags=["devices"])

DOMAIN = os.getenv("DOMAIN", "mondomaine.com")


@router.get("/", response_model=List[DeviceResponse])
def list_devices(db: Session = Depends(get_db)):
    return db.query(Device).order_by(Device.created_at.desc()).all()


@router.post("/", response_model=DeviceResponse, status_code=201)
async def create_device(payload: DeviceCreate, db: Session = Depends(get_db)):
    if db.query(Device).filter(Device.slug == payload.slug).first():
        raise HTTPException(status_code=409, detail="Ce slug est déjà utilisé")

    device = Device(
        **payload.model_dump(),
        public_url=f"https://{payload.slug}.{DOMAIN}",
        status="unknown",
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
