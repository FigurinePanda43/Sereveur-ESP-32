import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Device

logger = logging.getLogger(__name__)

MONITOR_INTERVAL = 60
SLOW_THRESHOLD = 3.0
HTTP_TIMEOUT = 5.0


async def check_device(db: Session, device: Device) -> str:
    url = f"http://{device.local_ip}:{device.local_port}"
    status = "offline"
    update_last_seen = False

    try:
        loop = asyncio.get_event_loop()
        start = loop.time()
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=HTTP_TIMEOUT)
        elapsed = loop.time() - start

        if resp.status_code < 500:
            status = "slow" if elapsed > SLOW_THRESHOLD else "online"
            update_last_seen = True
    except Exception:
        status = "offline"

    update_data: dict = {"status": status}
    if update_last_seen:
        update_data["last_seen"] = datetime.now(timezone.utc)

    db.query(Device).filter(Device.id == device.id).update(update_data)
    db.commit()
    return status


async def monitor_loop() -> None:
    while True:
        db = SessionLocal()
        try:
            devices = db.query(Device).all()
            if devices:
                results = await asyncio.gather(
                    *[check_device(db, d) for d in devices],
                    return_exceptions=True,
                )
                logger.info(
                    "Surveillance : %d équipement(s) — %s",
                    len(devices),
                    {d.slug: r for d, r in zip(devices, results)},
                )
        except Exception as exc:
            logger.error("Erreur monitor : %s", exc)
        finally:
            db.close()

        await asyncio.sleep(MONITOR_INTERVAL)
