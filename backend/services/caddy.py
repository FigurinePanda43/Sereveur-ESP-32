import logging
import os
from datetime import datetime
from typing import List

import httpx

logger = logging.getLogger(__name__)

CADDY_ADMIN_URL = os.getenv("CADDY_ADMIN_URL", "http://caddy:2019")


def _proxy_block(device) -> list:
    proto = getattr(device, "local_protocol", "http") or "http"
    dial = f"{device.local_ip}:{device.local_port}"
    if proto == "https":
        return [
            f"    reverse_proxy https://{dial} {{",
            "        transport http {",
            "            tls_insecure_skip_verify",
            "        }",
            "    }",
        ]
    return [f"    reverse_proxy {dial}"]


def _build_caddyfile(devices: List) -> str:
    domain = os.getenv("DOMAIN", "mondomaine.com")
    admin_domain = f"iot.{domain}"

    lines = [
        "{",
        "    admin 0.0.0.0:2019",
        "    auto_https off",
        "}",
        "",
        f"http://{admin_domain} {{",
        "    reverse_proxy backend:8000",
        "}",
        "",
    ]

    now = datetime.utcnow()

    for device in devices:
        device_domain = f"{device.slug}.{domain}"
        mode = device.access_mode or "protected"

        if mode == "public_temporary":
            if not device.public_until or device.public_until <= now:
                mode = "protected"

        if mode == "suspended":
            lines += [
                f"http://{device_domain} {{",
                "    rewrite * /device-suspended",
                "    reverse_proxy backend:8000",
                "}",
                "",
            ]
        elif mode == "protected":
            lines += [
                f"http://{device_domain} {{",
                "    forward_auth backend:8000 {",
                "        uri /auth/check",
                "        header_up -Connection",
                "        header_up -Upgrade",
                "    }",
                *_proxy_block(device),
                "}",
                "",
            ]
        elif mode in ("public_temporary", "public"):
            lines += [
                f"http://{device_domain} {{",
                *_proxy_block(device),
                "}",
                "",
            ]

    return "\n".join(lines)


async def sync_caddy(devices: List) -> bool:
    caddyfile = _build_caddyfile(devices)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{CADDY_ADMIN_URL}/load",
                content=caddyfile.encode(),
                headers={"Content-Type": "text/caddyfile"},
                timeout=10,
            )
        ok = resp.status_code == 200
        if ok:
            logger.info("Caddy synchronisé (%d équipement(s))", len(devices))
        else:
            logger.error("Échec synchronisation Caddy : %s", resp.text)
        return ok
    except Exception as exc:
        logger.error("Caddy inaccessible : %s", exc)
        return False
