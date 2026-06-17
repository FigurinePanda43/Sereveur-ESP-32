import logging
import os

import httpx

logger = logging.getLogger(__name__)

CF_API = "https://api.cloudflare.com/client/v4"


def _get_headers() -> dict:
    token = os.getenv("CLOUDFLARE_API_TOKEN", "")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _is_configured() -> bool:
    return all([
        os.getenv("CLOUDFLARE_API_TOKEN"),
        os.getenv("CLOUDFLARE_ZONE_ID"),
        os.getenv("CLOUDFLARE_TUNNEL_ID"),
    ])


async def create_dns_record(slug: str) -> bool:
    if not _is_configured():
        logger.warning("Cloudflare non configuré — création DNS ignorée pour '%s'", slug)
        return False

    zone_id = os.getenv("CLOUDFLARE_ZONE_ID")
    tunnel_id = os.getenv("CLOUDFLARE_TUNNEL_ID")
    domain = os.getenv("DOMAIN", "mondomaine.com")
    name = f"{slug}.{domain}"
    content = f"{tunnel_id}.cfargotunnel.com"

    async with httpx.AsyncClient() as client:
        existing = await client.get(
            f"{CF_API}/zones/{zone_id}/dns_records",
            headers=_get_headers(),
            params={"name": name},
            timeout=10,
        )
        if existing.status_code == 200 and existing.json().get("result"):
            logger.info("DNS déjà présent : %s", name)
            return True

        resp = await client.post(
            f"{CF_API}/zones/{zone_id}/dns_records",
            headers=_get_headers(),
            json={"type": "CNAME", "name": name, "content": content, "proxied": True, "ttl": 1},
            timeout=10,
        )

    if resp.status_code in (200, 201):
        logger.info("DNS créé : %s → %s", name, content)
        return True

    logger.error("Échec création DNS '%s' : %s", name, resp.text)
    return False


async def delete_dns_record(slug: str) -> bool:
    if not _is_configured():
        logger.warning("Cloudflare non configuré — suppression DNS ignorée pour '%s'", slug)
        return False

    zone_id = os.getenv("CLOUDFLARE_ZONE_ID")
    domain = os.getenv("DOMAIN", "mondomaine.com")
    name = f"{slug}.{domain}"

    async with httpx.AsyncClient() as client:
        search = await client.get(
            f"{CF_API}/zones/{zone_id}/dns_records",
            headers=_get_headers(),
            params={"name": name},
            timeout=10,
        )
        records = search.json().get("result", [])

        if not records:
            logger.info("DNS '%s' introuvable, rien à supprimer", name)
            return True

        record_id = records[0]["id"]
        resp = await client.delete(
            f"{CF_API}/zones/{zone_id}/dns_records/{record_id}",
            headers=_get_headers(),
            timeout=10,
        )

    if resp.status_code == 200:
        logger.info("DNS supprimé : %s", name)
        return True

    logger.error("Échec suppression DNS '%s' : %s", name, resp.text)
    return False
