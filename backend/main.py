import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from auth import auth_middleware
from database import Base, SessionLocal, engine
from models import Device
from routers.auth import router as auth_router
from routers.devices import router as devices_router
from services import caddy, monitor
from services.access_expiry import expiry_loop

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)


def _validate_config():
    secret = os.getenv("APP_SECRET_KEY", "")
    pw_hash = os.getenv("ADMIN_PASSWORD_HASH", "")
    errors = []
    if not secret or secret == "changeme":
        errors.append("APP_SECRET_KEY doit être défini et différent de 'changeme'")
    if not pw_hash:
        errors.append("ADMIN_PASSWORD_HASH doit être défini")
    if errors:
        for e in errors:
            logger.error("FATAL: %s", e)
        sys.exit(1)


async def _wait_for_caddy(retries: int = 10, delay: float = 2.0) -> bool:
    import httpx
    admin_url = os.getenv("CADDY_ADMIN_URL", "http://caddy:2019")
    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{admin_url}/config/", timeout=3)
            if resp.status_code == 200:
                logger.info("Caddy disponible après %d tentative(s)", attempt)
                return True
        except Exception:
            pass
        logger.info("Attente Caddy (%d/%d)…", attempt, retries)
        await asyncio.sleep(delay)
    logger.error("Caddy indisponible après %d tentatives", retries)
    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_config()

    Base.metadata.create_all(bind=engine)

    # Migrations SQLite
    with engine.connect() as conn:
        for sql in [
            "ALTER TABLE devices ADD COLUMN access_mode TEXT NOT NULL DEFAULT 'protected'",
            "ALTER TABLE devices ADD COLUMN public_until DATETIME NULL",
            "ALTER TABLE devices ADD COLUMN last_access_mode_change DATETIME NULL",
        ]:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                pass

    await _wait_for_caddy()

    db = SessionLocal()
    try:
        devices = db.query(Device).all()
        await caddy.sync_caddy(devices)
    finally:
        db.close()

    monitor_task = asyncio.create_task(monitor.monitor_loop())
    expiry_task = asyncio.create_task(expiry_loop())

    yield

    for task in (monitor_task, expiry_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="ESP32 Manager", version="2.0.0", lifespan=lifespan)

app.add_middleware(BaseHTTPMiddleware, dispatch=auth_middleware)

app.include_router(auth_router)
app.include_router(devices_router)


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "2.0.0"}


app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
