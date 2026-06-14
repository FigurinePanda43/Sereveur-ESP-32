import asyncio
import os

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/system", tags=["system"])

PROJECT_DIR = os.getenv("HOST_PROJECT_DIR", "/host-project")
COMPOSE_FILE = os.path.join(PROJECT_DIR, "docker-compose.yml")


async def _stream_command(cmd: list[str], cwd: str):
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    async for line in proc.stdout:
        yield line.decode(errors="replace")
    await proc.wait()
    yield f"\n[EXIT {proc.returncode}]\n"


async def _update_generator():
    yield "=== git pull ===\n"
    async for line in _stream_command(["git", "pull", "origin", "main"], cwd=PROJECT_DIR):
        yield line

    yield "\n=== docker compose up -d --build ===\n"
    async for line in _stream_command(
        ["docker", "compose", "-f", COMPOSE_FILE, "up", "-d", "--build"],
        cwd=PROJECT_DIR,
    ):
        yield line

    yield "\n=== Mise à jour terminée — le serveur redémarre ===\n"


@router.post("/update")
async def update():
    return StreamingResponse(
        _update_generator(),
        media_type="text/plain; charset=utf-8",
        headers={"X-Accel-Buffering": "no"},
    )
