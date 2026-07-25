import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from auth import hash_password
from database import get_db
from models import AccessLog, Device, User, UserDeviceAccess
from schemas import UserCreate, UserResponse, UserUpdate

router = APIRouter(prefix="/api/users", tags=["users"])

ADMIN_USER = os.getenv("ADMIN_USER", "admin")


def _to_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalise une date en UTC naïf pour rester cohérent avec datetime.utcnow()."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _serialize(user: User) -> dict:
    now = datetime.utcnow()
    devices = [access.device for access in user.accesses if access.device is not None]
    return {
        "id": user.id,
        "username": user.username,
        "enabled": user.enabled,
        "valid_until": user.valid_until,
        "expired": bool(user.valid_until and user.valid_until <= now),
        "description": user.description or "",
        "created_at": user.created_at,
        "last_login": user.last_login,
        "devices": sorted(devices, key=lambda d: d.project_name.lower()),
    }


def _resolve_devices(db: Session, device_ids: List[int]) -> List[Device]:
    unique_ids = list(dict.fromkeys(device_ids))
    devices = db.query(Device).filter(Device.id.in_(unique_ids)).all() if unique_ids else []
    found = {d.id for d in devices}
    missing = [i for i in unique_ids if i not in found]
    if missing:
        raise HTTPException(status_code=400, detail=f"Service(s) introuvable(s) : {missing}")
    return devices


@router.get("/", response_model=List[UserResponse])
def list_users(db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [_serialize(u) for u in users]


@router.post("/", response_model=UserResponse, status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db)):
    if payload.username == ADMIN_USER:
        raise HTTPException(status_code=409, detail="Cet identifiant est réservé à l'administrateur")
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=409, detail="Cet identifiant est déjà utilisé")

    devices = _resolve_devices(db, payload.device_ids)

    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        valid_until=_to_naive_utc(payload.valid_until),
        description=payload.description,
        enabled=True,
    )
    db.add(user)
    db.flush()

    for device in devices:
        db.add(UserDeviceAccess(user_id=user.id, device_id=device.id))

    db.add(AccessLog(
        event_type="user_created",
        message=f"Utilisateur « {user.username} » créé ({len(devices)} service(s))",
    ))
    db.commit()
    db.refresh(user)
    return _serialize(user)


@router.get("/{user_id}", response_model=UserResponse)
def get_user(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")
    return _serialize(user)


@router.put("/{user_id}", response_model=UserResponse)
def update_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")

    fields = payload.model_dump(exclude_unset=True)

    if "password" in fields and fields["password"]:
        user.password_hash = hash_password(fields["password"])
    if "enabled" in fields:
        user.enabled = fields["enabled"]
    if "description" in fields:
        user.description = fields["description"] or ""
    if "valid_until" in fields:
        user.valid_until = _to_naive_utc(payload.valid_until)

    if "device_ids" in fields and payload.device_ids is not None:
        devices = _resolve_devices(db, payload.device_ids)
        wanted = {d.id for d in devices}
        current = {a.device_id for a in user.accesses}
        for access in list(user.accesses):
            if access.device_id not in wanted:
                db.delete(access)
        for device_id in wanted - current:
            db.add(UserDeviceAccess(user_id=user.id, device_id=device_id))

    db.add(AccessLog(
        event_type="user_updated",
        message=f"Utilisateur « {user.username} » modifié",
    ))
    db.commit()
    db.refresh(user)
    return _serialize(user)


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")

    username = user.username
    db.delete(user)  # cascade supprime les accès associés
    db.add(AccessLog(
        event_type="user_deleted",
        message=f"Utilisateur « {username} » supprimé",
    ))
    db.commit()
