import asyncio
import logging
from datetime import datetime

from database import SessionLocal
from models import AccessLog, Device
from services import caddy

logger = logging.getLogger(__name__)


async def expiry_loop():
    while True:
        await asyncio.sleep(30)
        db = SessionLocal()
        try:
            now = datetime.utcnow()
            expired = db.query(Device).filter(
                Device.access_mode == "public_temporary",
                Device.public_until <= now,
            ).all()

            if expired:
                for device in expired:
                    log = AccessLog(
                        device_id=device.id,
                        event_type="public_access_expired",
                        old_mode="public_temporary",
                        new_mode="protected",
                        message=f"Accès public expiré pour {device.slug}",
                    )
                    db.add(log)
                    device.access_mode = "protected"
                    device.public_until = None
                    device.last_access_mode_change = now

                db.commit()
                all_devices = db.query(Device).all()
                await caddy.sync_caddy(all_devices)
                logger.info("Expiration : %d équipement(s) repassés en protected", len(expired))
        except Exception as exc:
            logger.error("Erreur expiry loop : %s", exc)
        finally:
            db.close()
