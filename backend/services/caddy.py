import logging
import os
from typing import List

import httpx

logger = logging.getLogger(__name__)

CADDY_ADMIN_URL = os.getenv("CADDY_ADMIN_URL", "http://caddy:2019")


def _build_config(devices: List) -> dict:
    domain = os.getenv("DOMAIN", "mondomaine.com")
    routes = [
        {
            "match": [{"host": [f"iot.{domain}"]}],
            "handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "backend:8000"}]}],
        }
    ]
    for device in devices:
        if not device.enabled:
            continue
        routes.append({
            "match": [{"host": [f"{device.slug}.{domain}"]}],
            "handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": f"{device.local_ip}:{device.local_port}"}]}],
        })
    return {
        "admin": {"listen": "0.0.0.0:2019"},
        "apps": {
            "http": {
                "servers": {
                    "main": {"listen": [":80"], "routes": routes}
                }
            }
        },
    }


async def sync_caddy(devices: List) -> bool:
    config = _build_config(devices)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{CADDY_ADMIN_URL}/load", json=config, timeout=10)
        if resp.status_code == 200:
            logger.info("Caddy synchronisé (%d équipement(s))", len(devices))
            return True
        logger.error("Échec synchronisation Caddy : %s", resp.text)
        return False
    except Exception as exc:
        logger.error("Caddy inaccessible : %s", exc)
        return False
