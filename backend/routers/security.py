"""API de l'onglet Sécurité : réglages IPBan, bannissements, listes d'accès,
compte-rendu des attaques et journal.

Toutes les routes sont sous ``/api/`` : réservées à l'administrateur par le
middleware de session. Chaque modification qui pourrait couper l'accès de
l'administrateur est d'abord évaluée sur sa propre connexion et refusée
(409) si elle le bloquerait : on ne s'enferme pas dehors depuis l'interface.
"""
import json
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from auth import get_client_country, get_client_ip
from database import get_db
from models import AuthAttempt, BlockedIP, Device, IPRule, SecurityEvent
from schemas import BanCreate, IPRuleCreate, IPTestRequest, SecuritySettingsUpdate
from services import caddy, ipban

router = APIRouter(prefix="/api/security", tags=["security"])

LIST_LABELS = {"allow": "Liste blanche", "deny": "Liste noire"}

_SETTING_LABELS = {
    "mode": "mode de fonctionnement",
    "apply_to_services": "périmètre",
    "exempt_private": "exemption du réseau local",
    "country_mode": "filtrage par pays",
    "countries": "pays",
    "bruteforce_enabled": "anti-force brute",
    "rules": "règles de blocage",
    "escalation_factor": "bannissement progressif",
    "max_ban_minutes": "durée maximale",
    "permanent_after": "bannissement définitif",
    "retention_days": "conservation du journal",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _client(request: Request):
    return ipban.normalize_ip(get_client_ip(request)), get_client_country(request)


def _probe(state: ipban.State) -> ipban.State:
    # Le levier IPBAN_DISABLED ne doit pas masquer un verrouillage : on évalue
    # comme si IPBan était réellement en service.
    return replace(state, forced_off=False)


def _guard_self(db: Session, request: Request, what: str) -> None:
    """Refuse une modification (pas encore validée) qui bloquerait l'administrateur."""
    db.flush()
    ip, country = _client(request)
    decision = ipban.evaluate(_probe(ipban.load_state(db)), ip, country, "portal")
    if not decision.allowed:
        db.rollback()
        where = f", pays {country}" if country else ""
        raise HTTPException(
            status_code=409,
            detail=f"{what} bloquerait votre propre accès ({decision.label} — IP {ip}{where}). "
                   "Ajoutez d'abord votre adresse à la liste blanche.",
        )


async def _resync_caddy(db: Session) -> None:
    await caddy.sync_caddy(db.query(Device).all())


def _settings_dict(s) -> dict:
    return {
        "enabled": bool(s.enabled),
        "forced_off": ipban.forced_off(),
        "mode": s.mode,
        "apply_to_services": bool(s.apply_to_services),
        "exempt_private": bool(s.exempt_private),
        "country_mode": s.country_mode,
        "countries": ipban.countries_of(s),
        "bruteforce_enabled": bool(s.bruteforce_enabled),
        "rules": ipban.parse_rules(s.rules),
        "escalation_factor": float(s.escalation_factor or 1),
        "max_ban_minutes": int(s.max_ban_minutes or 0),
        "permanent_after": int(s.permanent_after or 0),
        "retention_days": int(s.retention_days or 30),
        "updated_at": s.updated_at,
    }


def _settings_payload(db: Session, request: Request) -> dict:
    s = ipban.get_settings(db)
    state = ipban.load_state(db)
    ip, country = _client(request)
    decision = ipban.evaluate(_probe(state), ip, country, "portal")
    return {
        "settings": _settings_dict(s),
        "client": {
            "ip": ip,
            "country": country,
            "allowed": decision.allowed,
            "code": decision.code,
            "label": decision.label,
        },
        "counts": {
            "allow": len(state.allow),
            "deny": len(state.deny),
            "active_bans": len(state.bans),
        },
    }


def _ban_dict(b: BlockedIP, now: datetime) -> dict:
    active = bool(b.permanent) or b.blocked_until > now
    return {
        "ip": b.ip_address,
        "country": b.country,
        "reason": b.reason or "",
        "source": b.source or "auto",
        "permanent": bool(b.permanent),
        "active": active,
        "blocked_until": None if b.permanent else b.blocked_until,
        "since": b.updated_at or b.created_at,
        "ban_count": b.ban_count or 1,
    }


def _rule_dict(r: IPRule) -> dict:
    return {"id": r.id, "list_type": r.list_type, "value": r.value, "note": r.note or "", "created_at": r.created_at}


# ── Réglages ─────────────────────────────────────────────────────────────────

@router.get("/settings")
def get_settings(request: Request, db: Session = Depends(get_db)):
    return _settings_payload(db, request)


@router.put("/settings")
async def update_settings(payload: SecuritySettingsUpdate, request: Request, db: Session = Depends(get_db)):
    s = ipban.get_settings(db)
    guard_before = bool(s.enabled and s.apply_to_services)
    changed = []

    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        if key == "countries":
            value = ",".join(value)
        elif key == "rules":
            value = json.dumps(value)
        if getattr(s, key) != value:
            setattr(s, key, value)
            changed.append(key)

    if s.country_mode == "allow" and not ipban.countries_of(s):
        db.rollback()
        raise HTTPException(status_code=400, detail="Choisissez au moins un pays autorisé.")
    if s.bruteforce_enabled and not ipban.parse_rules(s.rules):
        db.rollback()
        raise HTTPException(status_code=400, detail="L'anti-force brute a besoin d'au moins une règle.")

    _guard_self(db, request, "Cette configuration")

    if changed:
        if "enabled" in changed:
            message = "IPBan activé" if s.enabled else "IPBan désactivé"
            others = [k for k in changed if k != "enabled"]
            if others:
                message += " · " + ", ".join(_SETTING_LABELS[k] for k in others)
        else:
            message = "Réglages modifiés : " + ", ".join(_SETTING_LABELS[k] for k in changed)
        ip, country = _client(request)
        ipban.log_event(db, "settings", message, ip, country, "admin")

    db.commit()
    ipban.invalidate()

    if bool(s.enabled and s.apply_to_services) != guard_before:
        await _resync_caddy(db)

    return _settings_payload(db, request)


# ── Bannissements ────────────────────────────────────────────────────────────

@router.get("/bans")
def list_bans(include_expired: bool = False, db: Session = Depends(get_db)):
    now = datetime.utcnow()
    q = db.query(BlockedIP)
    if not include_expired:
        q = q.filter(or_(BlockedIP.permanent == True, BlockedIP.blocked_until > now))  # noqa: E712
    rows = q.order_by(func.coalesce(BlockedIP.updated_at, BlockedIP.created_at).desc()).limit(500).all()
    return [_ban_dict(b, now) for b in rows]


@router.post("/bans", status_code=201)
def create_ban(payload: BanCreate, request: Request, db: Session = Depends(get_db)):
    ip = ipban.normalize_ip(payload.ip)
    admin_ip, _ = _client(request)
    if ip == admin_ip:
        raise HTTPException(status_code=409, detail="Vous ne pouvez pas bannir votre propre adresse IP.")

    decision = ipban.evaluate(_probe(ipban.load_state(db)), ip)
    if decision.code == "whitelisted":
        raise HTTPException(status_code=409, detail="Cette IP est en liste blanche : retirez-la d'abord.")
    if decision.code == "private":
        raise HTTPException(
            status_code=409,
            detail="Les adresses du réseau local sont exemptées : désactivez l'exemption pour les bannir.",
        )

    row = ipban.ban_ip(db, ip, payload.duration_minutes, payload.reason.strip())
    db.commit()
    db.refresh(row)
    ipban.invalidate()
    return _ban_dict(row, datetime.utcnow())


@router.delete("/bans/{ip}", status_code=204)
def delete_ban(ip: str, db: Session = Depends(get_db)):
    if not ipban.unban_ip(db, ip):
        raise HTTPException(status_code=404, detail="Cette IP n'est pas bannie.")
    db.commit()
    ipban.invalidate()
    return Response(status_code=204)


# ── Listes blanche / noire ───────────────────────────────────────────────────

@router.get("/lists")
def get_lists(db: Session = Depends(get_db)):
    rows = db.query(IPRule).order_by(IPRule.created_at.desc(), IPRule.id.desc()).all()
    return {
        "allow": [_rule_dict(r) for r in rows if r.list_type == "allow"],
        "deny": [_rule_dict(r) for r in rows if r.list_type == "deny"],
    }


@router.post("/lists", status_code=201)
def add_list_entry(payload: IPRuleCreate, request: Request, db: Session = Depends(get_db)):
    value = ipban.normalize_network(payload.value)
    exists = db.query(IPRule).filter(IPRule.list_type == payload.list_type, IPRule.value == value).first()
    if exists:
        raise HTTPException(status_code=409, detail=f"{value} figure déjà dans la {LIST_LABELS[payload.list_type].lower()}.")

    rule = IPRule(list_type=payload.list_type, value=value, note=payload.note.strip())
    db.add(rule)
    _guard_self(db, request, "Cet ajout")

    ip, country = _client(request)
    ipban.log_event(db, "list", f"{LIST_LABELS[payload.list_type]} : ajout de {value}", ip, country, payload.list_type)
    db.commit()
    db.refresh(rule)
    ipban.invalidate()
    return _rule_dict(rule)


@router.delete("/lists/{rule_id}", status_code=204)
def delete_list_entry(rule_id: int, request: Request, db: Session = Depends(get_db)):
    rule = db.query(IPRule).filter(IPRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Entrée introuvable.")
    list_type, value = rule.list_type, rule.value
    db.delete(rule)
    _guard_self(db, request, "Ce retrait")

    ip, country = _client(request)
    ipban.log_event(db, "list", f"{LIST_LABELS[list_type]} : retrait de {value}", ip, country, list_type)
    db.commit()
    ipban.invalidate()
    return Response(status_code=204)


# ── Test d'une adresse ───────────────────────────────────────────────────────

@router.post("/test")
def test_ip(payload: IPTestRequest, db: Session = Depends(get_db)):
    state = ipban.load_state(db)
    country = ipban.normalize_country(payload.country) if payload.country else None
    decision = ipban.evaluate(_probe(state), payload.ip, country, payload.scope)
    return {
        "ip": ipban.normalize_ip(payload.ip),
        "country": country,
        "scope": payload.scope,
        "allowed": decision.allowed,
        "code": decision.code,
        "label": decision.label,
        "detail": decision.detail,
        "until": decision.until,
        "effective": state.active,
    }


# ── Compte-rendu ─────────────────────────────────────────────────────────────

_PERIODS = {
    "24h": (timedelta(hours=24), "%Y-%m-%d %H", 24),
    "7d": (timedelta(days=7), "%Y-%m-%d", 7),
    "30d": (timedelta(days=30), "%Y-%m-%d", 30),
}


def _buckets(now: datetime, fmt: str, n: int) -> list:
    if fmt.endswith("%H"):
        top = now.replace(minute=0, second=0, microsecond=0)
        starts = [top - timedelta(hours=i) for i in range(n - 1, -1, -1)]
    else:
        top = now.replace(hour=0, minute=0, second=0, microsecond=0)
        starts = [top - timedelta(days=i) for i in range(n - 1, -1, -1)]
    return [(d.strftime(fmt), d) for d in starts]


@router.get("/report")
def report(period: str = Query("24h", pattern="^(24h|7d|30d)$"), db: Session = Depends(get_db)):
    ipban.flush_hits(db)  # inclut les requêtes bloquées pas encore écrites

    delta, fmt, n = _PERIODS[period]
    now = datetime.utcnow()
    since = now - delta

    failed = db.query(AuthAttempt).filter(AuthAttempt.success == False, AuthAttempt.created_at >= since)  # noqa: E712
    blocked = db.query(SecurityEvent).filter(SecurityEvent.event_type == "blocked", SecurityEvent.created_at >= since)
    bans = db.query(SecurityEvent).filter(SecurityEvent.event_type.in_(("ban", "manual_ban")), SecurityEvent.created_at >= since)

    failed_total = failed.count()
    success_total = db.query(AuthAttempt).filter(
        AuthAttempt.success == True, AuthAttempt.created_at >= since  # noqa: E712
    ).count()
    blocked_total = int(blocked.with_entities(func.coalesce(func.sum(SecurityEvent.count), 0)).scalar() or 0)
    bans_total = bans.count()

    # Chronologie
    f_by = dict(failed.with_entities(func.strftime(fmt, AuthAttempt.created_at), func.count())
                .group_by(func.strftime(fmt, AuthAttempt.created_at)).all())
    b_by = dict(blocked.with_entities(func.strftime(fmt, SecurityEvent.created_at), func.sum(SecurityEvent.count))
                .group_by(func.strftime(fmt, SecurityEvent.created_at)).all())
    n_by = dict(bans.with_entities(func.strftime(fmt, SecurityEvent.created_at), func.count())
                .group_by(func.strftime(fmt, SecurityEvent.created_at)).all())
    timeline = [
        {"start": start, "failed": int(f_by.get(key, 0)), "blocked": int(b_by.get(key, 0) or 0),
         "bans": int(n_by.get(key, 0))}
        for key, start in _buckets(now, fmt, n)
    ]

    # Adresses les plus actives (échecs + requêtes bloquées)
    ips: dict = {}
    for ip, count, country in failed.with_entities(
        AuthAttempt.ip_address, func.count(), func.max(AuthAttempt.country)
    ).group_by(AuthAttempt.ip_address).all():
        ips.setdefault(ip, {"ip": ip, "failed": 0, "blocked": 0, "country": None})
        ips[ip]["failed"] += count
        ips[ip]["country"] = ips[ip]["country"] or country
    for ip, count, country in blocked.with_entities(
        SecurityEvent.ip_address, func.sum(SecurityEvent.count), func.max(SecurityEvent.country)
    ).group_by(SecurityEvent.ip_address).all():
        if not ip or ip == "*":
            continue
        ips.setdefault(ip, {"ip": ip, "failed": 0, "blocked": 0, "country": None})
        ips[ip]["blocked"] += int(count or 0)
        ips[ip]["country"] = ips[ip]["country"] or country

    banned = ipban.load_state(db).bans
    top_ips = sorted(ips.values(), key=lambda r: (r["failed"] + r["blocked"], r["failed"]), reverse=True)[:10]
    for row in top_ips:
        row["banned"] = row["ip"] in banned

    countries: dict = {}
    for country, count in failed.with_entities(AuthAttempt.country, func.count()).group_by(AuthAttempt.country).all():
        key = country or ipban.UNKNOWN_COUNTRY
        countries[key] = countries.get(key, 0) + count
    for country, count in blocked.with_entities(SecurityEvent.country, func.sum(SecurityEvent.count)).group_by(SecurityEvent.country).all():
        key = country or ipban.UNKNOWN_COUNTRY
        countries[key] = countries.get(key, 0) + int(count or 0)
    top_countries = [
        {"country": c, "count": v}
        for c, v in sorted(countries.items(), key=lambda kv: kv[1], reverse=True)[:10]
    ]

    top_usernames = [
        {"username": u, "count": c}
        for u, c in failed.with_entities(AuthAttempt.username, func.count())
        .group_by(AuthAttempt.username).order_by(func.count().desc()).limit(10).all()
    ]

    reasons = [
        {"code": code, "label": ipban.REASON_LABELS.get(code, code), "count": int(c or 0)}
        for code, c in blocked.with_entities(SecurityEvent.reason, func.sum(SecurityEvent.count))
        .group_by(SecurityEvent.reason).order_by(func.sum(SecurityEvent.count).desc()).all()
    ]

    return {
        "period": period,
        "since": since,
        "totals": {
            "failed_logins": failed_total,
            "successful_logins": success_total,
            "blocked_requests": blocked_total,
            "bans": bans_total,
            "unique_attackers": len(ips),
            "active_bans": len(banned),
        },
        "timeline": timeline,
        "top_ips": top_ips,
        "top_countries": top_countries,
        "top_usernames": top_usernames,
        "block_reasons": reasons,
    }


# ── Journal ──────────────────────────────────────────────────────────────────

_EVENT_KINDS = {
    "blocked": ("blocked",),
    "ban": ("ban", "manual_ban", "unban"),
    "admin": ("settings", "list"),
}


@router.get("/events")
def events(
    kind: str = Query("all", pattern="^(all|blocked|ban|admin|auth_failed)$"),
    limit: int = Query(50, ge=1, le=200),
    before: Optional[datetime] = None,
    db: Session = Depends(get_db),
):
    ipban.flush_hits(db)
    if before is not None and before.tzinfo is not None:
        before = before.replace(tzinfo=None)

    items = []
    if kind != "auth_failed":
        q = db.query(SecurityEvent)
        if kind in _EVENT_KINDS:
            q = q.filter(SecurityEvent.event_type.in_(_EVENT_KINDS[kind]))
        if before is not None:
            q = q.filter(SecurityEvent.created_at < before)
        for e in q.order_by(SecurityEvent.created_at.desc(), SecurityEvent.id.desc()).limit(limit).all():
            items.append({
                "kind": e.event_type,
                "id": f"e{e.id}",
                "at": e.created_at,
                "last_seen": e.last_seen,
                "ip": e.ip_address,
                "country": e.country,
                "host": e.host,
                "path": e.path,
                "reason": e.reason,
                "message": e.message,
                "count": e.count or 1,
            })

    if kind in ("all", "auth_failed"):
        q = db.query(AuthAttempt).filter(AuthAttempt.success == False)  # noqa: E712
        if before is not None:
            q = q.filter(AuthAttempt.created_at < before)
        for a in q.order_by(AuthAttempt.created_at.desc(), AuthAttempt.id.desc()).limit(limit).all():
            items.append({
                "kind": "auth_failed",
                "id": f"a{a.id}",
                "at": a.created_at,
                "last_seen": None,
                "ip": a.ip_address,
                "country": a.country,
                "host": None,
                "path": None,
                "reason": a.failure_reason,
                "message": f"Échec de connexion « {a.username} »",
                "count": 1,
                "username": a.username,
            })

    items.sort(key=lambda i: i["at"] or datetime.min, reverse=True)
    items = items[:limit]
    next_before = items[-1]["at"] if len(items) == limit else None
    return {"items": items, "next_before": next_before}
