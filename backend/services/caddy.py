import logging
import os
from datetime import datetime
from typing import List

import httpx

logger = logging.getLogger(__name__)

CADDY_ADMIN_URL = os.getenv("CADDY_ADMIN_URL", "http://caddy:2019")

# Le cookie de session est déposé sur `.DOMAIN` : le navigateur l'envoie donc à
# CHAQUE sous-domaine d'équipement, et Caddy le transmet tel quel à l'équipement
# en amont. Un équipement compromis (firmware modifié, panneau vérolé) pourrait
# ainsi capter le cookie d'administration. Ce drapeau retire l'en-tête Cookie
# des requêtes proxifiées vers les équipements. Il est désactivé par défaut car
# il retire AUSSI les cookies propres à l'équipement (Proxmox, Home Assistant en
# ont besoin pour leur propre connexion). À activer si vos équipements protégés
# n'utilisent pas de cookie sur le domaine du portail. Le contrôle d'accès n'est
# pas affecté : forward_auth lit le cookie via une sous-requête distincte, avant
# ce retrait.
STRIP_COOKIES = os.getenv("PROXY_STRIP_COOKIES", "false").lower() in ("1", "true", "yes")


def _proxy_block(device) -> list:
    proto = getattr(device, "local_protocol", "http") or "http"
    dial = f"{device.local_ip}:{device.local_port}"
    upstream = f"https://{dial}" if proto == "https" else dial

    inner = []
    if proto == "https":
        inner += [
            "        transport http {",
            "            tls_insecure_skip_verify",
            "        }",
        ]
    if STRIP_COOKIES:
        inner.append("        header_up -Cookie")

    if not inner:
        return [f"    reverse_proxy {upstream}"]

    return [f"    reverse_proxy {upstream} {{", *inner, "    }"]


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
            # forward_auth s'applique à TOUTES les requêtes, y compris les
            # handshakes WebSocket. Auparavant, un matcher `@notws` excluait les
            # requêtes portant l'en-tête `Upgrade: websocket` de l'authentification :
            # n'importe qui pouvait alors atteindre un service protégé sans être
            # authentifié en ajoutant simplement cet en-tête (contournement total).
            lines += [
                f"http://{device_domain} {{",
                "    forward_auth backend:8000 {",
                "        uri /auth/check",
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
