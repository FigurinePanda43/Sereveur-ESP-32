import asyncio
import fcntl
import json
import logging
import os
import pty
import struct
import termios
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from auth import COOKIE_NAME, verify_token

logger = logging.getLogger(__name__)

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


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


@router.websocket("/terminal")
async def terminal_ws(websocket: WebSocket):
    # BaseHTTPMiddleware (our cookie auth) does not run for websocket scopes,
    # so the session cookie must be checked manually before accepting.
    token = websocket.cookies.get(COOKIE_NAME, "")
    if not verify_token(token):
        await websocket.close(code=4401)
        return

    await websocket.accept()
    logger.warning("Session terminal root ouverte")

    container_name = f"webterm-{uuid.uuid4().hex[:8]}"
    master_fd, slave_fd = pty.openpty()
    _set_winsize(slave_fd, 24, 80)

    proc = await asyncio.create_subprocess_exec(
        "docker", "run", "--rm", "-i", "-t",
        "--name", container_name,
        "--privileged",
        "--pid=host", "--net=host", "--ipc=host", "--uts=host",
        "-v", "/:/host",
        "alpine",
        "chroot", "/host", "/bin/sh", "-c", "exec bash -l 2>/dev/null || exec sh -l",
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        preexec_fn=os.setsid,
    )
    os.close(slave_fd)

    loop = asyncio.get_event_loop()
    closed = False

    async def pump_output():
        while not closed:
            try:
                data = await loop.run_in_executor(None, os.read, master_fd, 4096)
            except OSError:
                break
            if not data:
                break
            try:
                await websocket.send_bytes(data)
            except Exception:
                break

    reader_task = asyncio.create_task(pump_output())

    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if data is not None:
                os.write(master_fd, data)
                continue
            text = msg.get("text")
            if text is not None:
                try:
                    payload = json.loads(text)
                    if payload.get("type") == "resize":
                        _set_winsize(master_fd, int(payload["rows"]), int(payload["cols"]))
                except (ValueError, KeyError, TypeError):
                    pass
    except WebSocketDisconnect:
        pass
    finally:
        closed = True
        reader_task.cancel()
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            os.close(master_fd)
        except OSError:
            pass
        kill_proc = await asyncio.create_subprocess_exec(
            "docker", "kill", container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await kill_proc.wait()
        logger.warning("Session terminal root fermée")
