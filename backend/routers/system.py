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


async def _git_output(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=PROJECT_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode, out.decode(errors="replace").strip()


@router.get("/update-check")
async def update_check():
    rc, _ = await _git_output("fetch", "origin", "main", "--quiet")
    if rc != 0:
        return {"update_available": False, "error": "git fetch a échoué"}

    rc_local, local_commit = await _git_output("rev-parse", "HEAD")
    rc_remote, remote_commit = await _git_output("rev-parse", "origin/main")
    if rc_local != 0 or rc_remote != 0:
        return {"update_available": False, "error": "impossible de lire les commits"}

    rc_count, count_out = await _git_output("rev-list", "--count", "HEAD..origin/main")
    commits_behind = int(count_out) if rc_count == 0 and count_out.isdigit() else 0

    return {
        "update_available": commits_behind > 0,
        "commits_behind": commits_behind,
        "current_commit": local_commit[:7],
        "latest_commit": remote_commit[:7],
    }


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
