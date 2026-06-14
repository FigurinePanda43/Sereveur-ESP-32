import logging
import os
from datetime import datetime
from typing import List

import httpx

logger = logging.getLogger(__name__)

CADDY_ADMIN_URL = os.getenv("CADDY_ADMIN_URL", "http://caddy:2019")


def _build_config(devices: List) -> dict:
    domain = os.getenv("DOMAIN", "mondomaine.com")
    admin_domain = f"iot.{domain}"   # admin at iot.DOMAIN

    routes = [
        {
            "match": [{"host": [admin_domain]}],
            "handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "backend:8000"}]}],
        }
    ]

    now = datetime.utcnow()

    for device in devices:
        device_domain = f"{device.slug}.{domain}"  # devices at slug.DOMAIN
        mode = device.access_mode or "protected"

        # Treat expired public_temporary as protected
        if mode == "public_temporary":
            if not device.public_until or device.public_until <= now:
                mode = "protected"

        if mode == "suspended":
            routes.append({
                "match": [{"host": [device_domain]}],
                "handle": [{
                    "handler": "static_response",
                    "status_code": 403,
                    "body": "Device suspended",
                }],
            })
        elif mode == "protected":
            routes.append({
                "match": [{"host": [device_domain]}],
                "handle": [
                    {
                        "handler": "forward_auth",
                        "uri": "http://backend:8000/auth/check",
                    },
                    {
                        "handler": "reverse_proxy",
                        "upstreams": [{"dial": f"{device.local_ip}:{device.local_port}"}],
                    },
                ],
            })
        elif mode == "public_temporary":
            routes.append({
                "match": [{"host": [device_domain]}],
                "handle": [{
                    "handler": "reverse_proxy",
                    "upstreams": [{"dial": f"{device.local_ip}:{device.local_port}"}],
                }],
            })

    return {
        "admin": {"listen": "0.0.0.0:2019"},
        "apps": {"http": {"servers": {"main": {"listen": [":80"], "routes": routes}}}},
    }


async def sync_caddy(devices: List) -> bool:
    config = _build_config(devices)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{CADDY_ADMIN_URL}/load", json=config, timeout=10)
        ok = resp.status_code == 200
        if ok:
            logger.info("Caddy synchronisé (%d équipement(s))", len(devices))
        else:
            logger.error("Échec synchronisation Caddy : %s", resp.text)
        return ok
    except Exception as exc:
        logger.error("Caddy inaccessible : %s", exc)
        return False
