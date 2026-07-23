import asyncio
import fcntl
import ipaddress
import json
import logging
import os
import pty
import re
import struct
import termios
import uuid

import httpx
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
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


# ── Scan réseau local ──────────────────────────────────────────────────────────

# Ports web courants pour l'auto-hébergement (services domotique/média/admin).
WEB_PORTS = [
    80, 81, 443, 3000, 3001, 5000, 5001, 5601, 7878, 8000, 8006, 8008,
    8080, 8081, 8096, 8112, 8123, 8181, 8443, 8888, 8989, 9000, 9091, 1880, 2019, 32400,
]

# Ports où l'on tente HTTPS en premier (généralement en TLS).
_HTTPS_FIRST = {443, 8443, 8006}

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

# Limites de sûreté : évite de lancer un scan géant qui saturerait le réseau.
_MAX_HOSTS = 512
_TCP_TIMEOUT = 0.6
_HTTP_TIMEOUT = 2.5
_CONCURRENCY = 150


async def _probe_tcp(ip: str, port: int) -> bool:
    """Retourne True si le port TCP accepte une connexion."""
    try:
        fut = asyncio.open_connection(ip, port)
        _, writer = await asyncio.wait_for(fut, timeout=_TCP_TIMEOUT)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


def _extract_title(html: str) -> str | None:
    m = _TITLE_RE.search(html or "")
    if not m:
        return None
    title = re.sub(r"\s+", " ", m.group(1)).strip()
    return title[:80] or None


async def _http_info(ip: str, port: int) -> dict:
    """Sonde HTTP(S) un port ouvert pour confirmer une interface web + titre."""
    schemes = ["https", "http"] if port in _HTTPS_FIRST else ["http", "https"]
    for scheme in schemes:
        url = f"{scheme}://{ip}:{port}"
        try:
            async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
                resp = await client.get(url, timeout=_HTTP_TIMEOUT)
            return {
                "scheme": scheme,
                "status": resp.status_code,
                "title": _extract_title(resp.text),
                "is_web": True,
            }
        except Exception:
            continue
    # Port ouvert mais pas de réponse HTTP exploitable (autre protocole).
    return {"scheme": "http", "status": None, "title": None, "is_web": False}


async def _scan_generator(network: str):
    try:
        net = ipaddress.ip_network(network, strict=False)
    except ValueError:
        yield json.dumps({"type": "error", "message": f"Réseau invalide : {network}"}) + "\n"
        return

    hosts = [str(h) for h in net.hosts()] or [str(net.network_address)]
    if len(hosts) > _MAX_HOSTS:
        yield json.dumps({
            "type": "error",
            "message": f"Plage trop grande ({len(hosts)} hôtes, max {_MAX_HOSTS}). Utilisez un /23 ou plus petit.",
        }) + "\n"
        return

    total = len(hosts) * len(WEB_PORTS)
    sem = asyncio.Semaphore(_CONCURRENCY)
    queue: asyncio.Queue = asyncio.Queue()

    async def worker(ip: str, port: int):
        async with sem:
            is_open = await _probe_tcp(ip, port)
        if is_open:
            info = await _http_info(ip, port)
            await queue.put(("service", {"ip": ip, "port": port, **info}))
        await queue.put(("progress", None))

    tasks = [asyncio.create_task(worker(ip, port)) for ip in hosts for port in WEB_PORTS]

    async def waiter():
        await asyncio.gather(*tasks, return_exceptions=True)
        await queue.put(("done", None))

    waiter_task = asyncio.create_task(waiter())

    yield json.dumps({
        "type": "start", "total": total, "hosts": len(hosts), "ports": len(WEB_PORTS),
    }) + "\n"

    done = 0
    found = 0
    try:
        while True:
            kind, data = await queue.get()
            if kind == "done":
                break
            if kind == "progress":
                done += 1
                if done % 40 == 0 or done == total:
                    yield json.dumps({"type": "progress", "done": done, "total": total}) + "\n"
            elif kind == "service":
                found += 1
                yield json.dumps({"type": "service", **data}) + "\n"
    finally:
        waiter_task.cancel()
        for t in tasks:
            t.cancel()

    yield json.dumps({"type": "complete", "found": found}) + "\n"


@router.get("/scan-network")
async def scan_network(subnet: str = Query("192.168.1.0/24")):
    return StreamingResponse(
        _scan_generator(subnet),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
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
