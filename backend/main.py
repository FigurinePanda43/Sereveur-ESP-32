import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from auth import auth_middleware
from database import Base, SessionLocal, engine
from models import Device
from routers.devices import router as devices_router
from services import caddy, monitor

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)


async def _wait_for_caddy(retries: int = 10, delay: float = 2.0) -> bool:
    import httpx
    import os
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
    Base.metadata.create_all(bind=engine)

    # Migration : ajout colonne enabled si absente
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE devices ADD COLUMN enabled BOOLEAN NOT NULL DEFAULT 1"))
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

    yield

    monitor_task.cancel()
    try:
        await monitor_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="ESP32 Manager", version="1.0.0", lifespan=lifespan)

app.add_middleware(BaseHTTPMiddleware, dispatch=auth_middleware)

app.include_router(devices_router)


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
