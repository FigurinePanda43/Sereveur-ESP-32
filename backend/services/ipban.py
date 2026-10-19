"""IPBan : filtrage des adresses IP et protection anti-force brute.

Le trafic arrive toujours par Cloudflare Tunnel : l'IP réelle du client est
dans ``CF-Connecting-IP`` et son pays dans ``CF-IPCountry`` (géolocalisation
Cloudflare, sans base GeoIP locale). Toutes les requêtes passent par le
backend, qui décide :

- portail ``iot.DOMAIN`` : middleware d'authentification (``auth.py``) ;
- service protégé : ``forward_auth`` → ``/auth/check`` (même middleware) ;
- service public : ``forward_auth`` → ``/ipban/check``, ajouté par Caddy
  uniquement quand IPBan protège aussi les services.

Ordre d'évaluation (le premier qui s'applique l'emporte) :

1. IPBan désactivé, ou service hors périmètre → autorisé ;
2. IP en liste blanche → autorisée (jamais bannie, ni filtrée par pays) ;
3. IP privée / locale et exemption activée → autorisée ;
4. IP bannie (force brute ou bannissement manuel) → bloquée ;
5. IP en liste noire → bloquée ;
6. mode « liste blanche uniquement » → bloquée ;
7. filtrage par pays (autoriser seulement / bloquer) ;
8. sinon → autorisée.

Les décisions sont prises sur un instantané en mémoire (réglages, listes,
bannissements actifs), rechargé dès qu'une écriture l'invalide et au plus tard
toutes les ``CACHE_TTL`` secondes : un service public ne coûte pas une requête
SQL par hit. Les requêtes bloquées sont agrégées en mémoire puis écrites par
lots (``flush_hits``) : une rafale ne produit pas une ligne par requête.
"""
import asyncio
import ipaddress
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from database import SessionLocal
from models import AccessLog, AuthAttempt, BlockedIP, IPRule, SecurityEvent, SecuritySettings

logger = logging.getLogger(__name__)

# Fabrique de sessions utilisée hors requête (middleware, tâche de fond).
# Remplaçable par les tests, qui travaillent sur une base en mémoire.
session_factory = SessionLocal

CACHE_TTL = 15.0
FLUSH_INTERVAL = 30
PURGE_INTERVAL = 3600

# Un bannissement permanent garde une date de fin (colonne NOT NULL), très loin.
PERMANENT_UNTIL = datetime(9999, 12, 31)

MODES = ("standard", "whitelist")
COUNTRY_MODES = ("off", "allow", "block")

# Codes Cloudflare : ISO 3166-1 alpha-2, plus XX (inconnu) et T1 (Tor).
_COUNTRY_RE = re.compile(r"^[A-Z][A-Z0-9]$")
UNKNOWN_COUNTRY = "XX"

REASON_LABELS = {
    "allowed": "Autorisée",
    "disabled": "IPBan désactivé",
    "out_of_scope": "Hors périmètre",
    "unknown_ip": "IP indéterminée",
    "whitelisted": "Liste blanche",
    "private": "Réseau local",
    "banned": "IP bannie",
    "denylist": "Liste noire",
    "not_whitelisted": "Hors liste blanche",
    "country_blocked": "Pays bloqué",
    "country_not_allowed": "Pays non autorisé",
}


def forced_off() -> bool:
    """Levier de secours : ``IPBAN_DISABLED=true`` coupe IPBan quelle que soit
    la configuration en base (en cas de verrouillage de l'administrateur)."""
    return os.getenv("IPBAN_DISABLED", "").lower() in ("1", "true", "yes")


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def default_rules() -> list:
    """Paliers par défaut. Le premier reste réglable par l'environnement
    (``BRUTEFORCE_*``) pour la compatibilité avec les déploiements existants."""
    return [
        {
            "window_minutes": _env_int("BRUTEFORCE_WINDOW_MINUTES", 15),
            "max_failures": _env_int("BRUTEFORCE_MAX_ATTEMPTS", 10),
            "ban_minutes": _env_int("BRUTEFORCE_BLOCK_MINUTES", 15),
        },
        {"window_minutes": 60, "max_failures": 20, "ban_minutes": 60},
        {"window_minutes": 1440, "max_failures": 50, "ban_minutes": 1440},
    ]


def parse_rules(text: str) -> list:
    try:
        rules = json.loads(text or "[]")
        return [
            {
                "window_minutes": int(r["window_minutes"]),
                "max_failures": int(r["max_failures"]),
                "ban_minutes": int(r["ban_minutes"]),
            }
            for r in rules
        ]
    except (ValueError, TypeError, KeyError):
        return default_rules()


# ── Adresses et pays ─────────────────────────────────────────────────────────

def parse_ip(value: str):
    try:
        ip = ipaddress.ip_address((value or "").strip())
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip


def normalize_ip(value: str) -> str:
    """Forme canonique d'une IP (``::FFFF:1.2.3.4`` → ``1.2.3.4``) ; la valeur
    brute est conservée si elle n'est pas analysable."""
    ip = parse_ip(value)
    return str(ip) if ip is not None else (value or "")


def normalize_network(value: str) -> str:
    """IP ou plage CIDR normalisée. Une IP seule reste écrite sans préfixe."""
    net = ipaddress.ip_network((value or "").strip(), strict=False)
    if net.num_addresses == 1:
        return str(net.network_address)
    return str(net)


def normalize_country(value: Optional[str]) -> str:
    cc = (value or "").strip().upper()
    return cc if _COUNTRY_RE.match(cc) else UNKNOWN_COUNTRY


def is_private(ip) -> bool:
    return ip.is_private or ip.is_loopback or ip.is_link_local


def client_ip(conn) -> str:
    """IP du client derrière Cloudflare et Caddy (Request ou WebSocket)."""
    for header in ("cf-connecting-ip", "x-forwarded-for"):
        val = conn.headers.get(header, "")
        if val:
            return val.split(",")[0].strip()
    return conn.client.host if conn.client else "unknown"


def client_country(conn) -> Optional[str]:
    val = conn.headers.get("cf-ipcountry", "")
    return normalize_country(val) if val else None


# Sous-requêtes forward_auth de Caddy : l'hôte et le chemin d'origine arrivent
# dans X-Forwarded-Host / X-Forwarded-Uri. Ailleurs, ces en-têtes sont ignorés
# pour qu'un client ne puisse pas se faire passer pour une requête d'équipement.
FORWARD_AUTH_PATHS = ("/auth/check", "/ipban/check")


def _origin(conn) -> tuple:
    """(hôte, chemin) d'origine de la requête."""
    if conn.url.path in FORWARD_AUTH_PATHS:
        host = conn.headers.get("x-forwarded-host") or conn.headers.get("host", "")
        path = conn.headers.get("x-forwarded-uri") or conn.url.path
    else:
        host, path = conn.headers.get("host", ""), conn.url.path
    return host.split(":")[0].strip().lower(), path


def request_scope(conn) -> str:
    """``service`` pour un sous-domaine d'équipement, ``portal`` sinon."""
    host, _ = _origin(conn)
    domain = os.getenv("DOMAIN", "mondomaine.com").lower()
    if host.endswith(f".{domain}") and host != f"iot.{domain}":
        return "service"
    return "portal"


# ── Réglages ─────────────────────────────────────────────────────────────────

def get_settings(db) -> SecuritySettings:
    row = db.query(SecuritySettings).filter(SecuritySettings.id == 1).first()
    if row is not None:
        return row
    row = SecuritySettings(id=1, rules=json.dumps(default_rules()))
    db.add(row)
    try:
        db.commit()
    except IntegrityError:  # créée entre-temps par une autre requête
        db.rollback()
        return db.query(SecuritySettings).filter(SecuritySettings.id == 1).first()
    db.refresh(row)
    return row


def countries_of(settings: SecuritySettings) -> list:
    return sorted({c for c in (settings.countries or "").split(",") if c})


# ── Instantané et décision ───────────────────────────────────────────────────

@dataclass
class Ban:
    until: datetime
    permanent: bool
    reason: str


@dataclass
class State:
    enabled: bool = False
    forced_off: bool = False
    mode: str = "standard"
    apply_to_services: bool = True
    exempt_private: bool = True
    country_mode: str = "off"
    countries: frozenset = frozenset()
    allow: list = field(default_factory=list)   # [(réseau, note)]
    deny: list = field(default_factory=list)
    bans: dict = field(default_factory=dict)    # ip → Ban

    @property
    def active(self) -> bool:
        return self.enabled and not self.forced_off


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: str
    detail: str = ""
    until: Optional[datetime] = None

    @property
    def label(self) -> str:
        return REASON_LABELS.get(self.code, self.code)


def load_state(db) -> State:
    """Instantané lu depuis la session donnée (y compris ses écritures non
    encore validées : sert aussi à tester une modification avant commit)."""
    s = get_settings(db)
    now = datetime.utcnow()
    allow, deny = [], []
    for rule in db.query(IPRule).all():
        try:
            net = ipaddress.ip_network(rule.value, strict=False)
        except ValueError:
            continue
        (allow if rule.list_type == "allow" else deny).append((net, rule.note or ""))
    bans = {}
    active = db.query(BlockedIP).filter(
        or_(BlockedIP.permanent == True, BlockedIP.blocked_until > now)  # noqa: E712
    ).all()
    for b in active:
        bans[normalize_ip(b.ip_address)] = Ban(b.blocked_until, bool(b.permanent), b.reason or "")
    return State(
        enabled=bool(s.enabled),
        forced_off=forced_off(),
        mode=s.mode if s.mode in MODES else "standard",
        apply_to_services=bool(s.apply_to_services),
        exempt_private=bool(s.exempt_private),
        country_mode=s.country_mode if s.country_mode in COUNTRY_MODES else "off",
        countries=frozenset(countries_of(s)),
        allow=allow,
        deny=deny,
        bans=bans,
    )


def _in_list(ip, entries):
    for net, note in entries:
        if ip.version == net.version and ip in net:
            return net, note
    return None


def evaluate(state: State, ip_value: str, country: Optional[str] = None,
             scope: str = "portal", now: Optional[datetime] = None) -> Decision:
    if not state.active:
        return Decision(True, "disabled")
    if scope == "service" and not state.apply_to_services:
        return Decision(True, "out_of_scope")

    ip = parse_ip(ip_value)
    if ip is None:
        return Decision(True, "unknown_ip")

    hit = _in_list(ip, state.allow)
    if hit:
        return Decision(True, "whitelisted", hit[1] or str(hit[0]))

    if state.exempt_private and is_private(ip):
        return Decision(True, "private")

    ban = state.bans.get(str(ip))
    now = now or datetime.utcnow()
    if ban and (ban.permanent or ban.until > now):
        return Decision(False, "banned", ban.reason, None if ban.permanent else ban.until)

    hit = _in_list(ip, state.deny)
    if hit:
        return Decision(False, "denylist", hit[1] or str(hit[0]))

    if state.mode == "whitelist":
        return Decision(False, "not_whitelisted")

    cc = normalize_country(country)
    if state.country_mode == "allow" and cc not in state.countries:
        return Decision(False, "country_not_allowed", cc)
    if state.country_mode == "block" and cc in state.countries:
        return Decision(False, "country_blocked", cc)

    return Decision(True, "allowed")


# ── Cache ────────────────────────────────────────────────────────────────────

_lock = threading.Lock()
_cache: Optional[State] = None
_cache_at = 0.0
_cache_gen = -1
_generation = 0


def invalidate() -> None:
    """À appeler après toute écriture qui change une décision."""
    global _generation
    with _lock:
        _generation += 1


def current_state() -> State:
    global _cache, _cache_at, _cache_gen
    with _lock:
        gen = _generation
        if _cache is not None and _cache_gen == gen and time.monotonic() - _cache_at < CACHE_TTL:
            return _cache
    db = session_factory()
    try:
        state = load_state(db)
    except Exception as exc:
        # Base indisponible : on laisse passer plutôt que de verrouiller tout le
        # monde dehors, administrateur compris. L'erreur reste visible au journal.
        logger.error("IPBan : lecture de l'état impossible (%s) — filtrage suspendu", exc)
        state = _cache or State()
    finally:
        db.close()
    with _lock:
        _cache, _cache_at, _cache_gen = state, time.monotonic(), gen
    return state


def public_guard() -> bool:
    """Vrai si Caddy doit placer un ``forward_auth`` IPBan devant les services
    publics (les services protégés passent déjà par ``/auth/check``)."""
    state = current_state()
    return state.active and state.apply_to_services


def check(conn) -> Optional[Decision]:
    """Décision pour une requête entrante ; ``None`` si elle passe. La requête
    bloquée est comptabilisée pour le journal."""
    state = current_state()
    if not state.active:
        return None
    ip = client_ip(conn)
    country = client_country(conn)
    decision = evaluate(state, ip, country, request_scope(conn))
    if decision.allowed:
        return None
    host, path = _origin(conn)
    note_block(normalize_ip(ip)[:64], country, decision, host[:255], path)
    return decision


# ── Journal des requêtes bloquées (agrégé) ───────────────────────────────────

_MAX_HIT_KEYS = 2000
_hits: dict = {}


def note_block(ip: str, country: Optional[str], decision: Decision, host: str, path: str) -> None:
    now = datetime.utcnow()
    key = (ip, decision.code, host)
    with _lock:
        # Au-delà du plafond (attaque très distribuée), on agrège sans l'IP
        # plutôt que de laisser la mémoire grossir sans limite.
        if key not in _hits and len(_hits) >= _MAX_HIT_KEYS:
            key = ("*", decision.code, "*")
        hit = _hits.get(key)
        if hit is None:
            _hits[key] = {
                "count": 1, "country": country, "path": (path or "")[:300],
                "detail": decision.detail, "first": now, "last": now,
            }
        else:
            hit["count"] += 1
            hit["last"] = now
            hit["path"] = (path or "")[:300]


def flush_hits(db) -> int:
    """Écrit les requêtes bloquées en attente. Une rafale sur la même cible se
    fond dans l'événement précédent tant qu'elle ne s'interrompt pas plus de
    10 minutes (et dans la même heure)."""
    global _hits
    with _lock:
        pending, _hits = _hits, {}
    for (ip, code, host), h in pending.items():
        # Fusion limitée à l'heure en cours : chaque ligne reste rattachée à une
        # seule tranche horaire, ce qui garde la chronologie du rapport exacte.
        hour = h["first"].replace(minute=0, second=0, microsecond=0)
        recent = db.query(SecurityEvent).filter(
            SecurityEvent.event_type == "blocked",
            SecurityEvent.ip_address == ip,
            SecurityEvent.reason == code,
            SecurityEvent.host == host,
            SecurityEvent.created_at >= hour,
            SecurityEvent.last_seen >= h["first"] - timedelta(minutes=10),
        ).order_by(SecurityEvent.id.desc()).first()
        if recent:
            recent.count = (recent.count or 0) + h["count"]
            recent.last_seen = h["last"]
            recent.path = h["path"]
        else:
            detail = f" ({h['detail']})" if h["detail"] else ""
            db.add(SecurityEvent(
                event_type="blocked",
                ip_address=ip,
                country=h["country"],
                host=host,
                path=h["path"],
                reason=code,
                message=f"{REASON_LABELS.get(code, code)}{detail}",
                count=h["count"],
                created_at=h["first"],
                last_seen=h["last"],
            ))
    if pending:
        db.commit()
    return len(pending)


# ── Bannissements ────────────────────────────────────────────────────────────

def log_event(db, event_type: str, message: str, ip: str = None, country: str = None,
              reason: str = None) -> None:
    now = datetime.utcnow()
    db.add(SecurityEvent(
        event_type=event_type, ip_address=ip, country=country, reason=reason,
        message=message, created_at=now, last_seen=now,
    ))


def _ban_duration(base_minutes: int, ban_count: int, settings: SecuritySettings) -> int:
    factor = max(1.0, float(settings.escalation_factor or 1.0))
    minutes = base_minutes * factor ** max(0, ban_count - 1)
    cap = max(base_minutes, int(settings.max_ban_minutes or base_minutes))
    return int(min(minutes, cap))


def _upsert_ban(db, ip: str, until: datetime, permanent: bool, reason: str,
                source: str, country: Optional[str], ban_count: int) -> BlockedIP:
    now = datetime.utcnow()
    row = db.query(BlockedIP).filter(BlockedIP.ip_address == ip).first()
    if row is None:
        row = BlockedIP(ip_address=ip)
        db.add(row)
    row.blocked_until = until
    row.permanent = permanent
    row.reason = reason
    row.source = source
    row.ban_count = ban_count
    row.country = country or row.country
    row.updated_at = now  # début du bannissement : les échecs antérieurs ne comptent plus
    return row


def register_failure(db, ip: str, user_agent: str = "", country: Optional[str] = None):
    """Applique les règles anti-force brute après un échec de connexion.
    Retourne ``(bloquée, fin_du_blocage_ou_None)``."""
    settings = get_settings(db)
    if forced_off() or not settings.enabled or not settings.bruteforce_enabled:
        return False, None

    state = load_state(db)
    if evaluate(state, ip, country).code in ("whitelisted", "private", "unknown_ip"):
        return False, None

    ip = normalize_ip(ip)
    now = datetime.utcnow()
    existing = db.query(BlockedIP).filter(BlockedIP.ip_address == ip).first()
    if existing and (existing.permanent or existing.blocked_until > now):
        return True, existing.blocked_until

    # Seuls les échecs postérieurs au dernier bannissement (ou déblocage) comptent :
    # sans cela, les échecs déjà sanctionnés redéclencheraient un bannissement
    # dès la première nouvelle tentative.
    floor = (existing.updated_at or existing.created_at) if existing else None

    best = None
    for rule in parse_rules(settings.rules):
        since = now - timedelta(minutes=rule["window_minutes"])
        if floor and floor > since:
            since = floor
        count = db.query(AuthAttempt).filter(
            AuthAttempt.ip_address == ip,
            AuthAttempt.success == False,  # noqa: E712
            AuthAttempt.created_at >= since,
        ).count()
        if count >= rule["max_failures"] and (best is None or rule["ban_minutes"] > best[2]):
            best = (rule["window_minutes"], count, rule["ban_minutes"])

    if best is None:
        return False, None

    window, count, base = best
    ban_count = (existing.ban_count or 1) + 1 if existing else 1
    permanent = bool(settings.permanent_after) and ban_count >= settings.permanent_after
    minutes = _ban_duration(base, ban_count, settings)
    until = PERMANENT_UNTIL if permanent else now + timedelta(minutes=minutes)

    reason = f"{count} échecs en {window} min"
    if ban_count > 1:
        reason += f" · récidive n°{ban_count}"
    _upsert_ban(db, ip, until, permanent, reason, "auto", country, ban_count)

    duration_txt = "définitif" if permanent else f"{minutes} min"
    log_event(db, "ban", f"Bannissement {duration_txt} : {reason}", ip, country, "bruteforce")
    db.add(AccessLog(
        event_type="ip_blocked", source_ip=ip, user_agent=user_agent,
        message=f"Blocage {duration_txt} : {reason}",
    ))
    db.commit()
    invalidate()
    logger.warning("IPBan : %s bannie (%s, %s)", ip, reason, duration_txt)
    return True, until


def ban_ip(db, ip: str, minutes: Optional[int], reason: str, country: Optional[str] = None) -> BlockedIP:
    """Bannissement manuel. ``minutes=None`` → définitif."""
    ip = normalize_ip(ip)
    existing = db.query(BlockedIP).filter(BlockedIP.ip_address == ip).first()
    ban_count = (existing.ban_count or 1) + 1 if existing else 1
    permanent = minutes is None
    until = PERMANENT_UNTIL if permanent else datetime.utcnow() + timedelta(minutes=minutes)
    row = _upsert_ban(db, ip, until, permanent, reason or "Bannissement manuel", "manual", country, ban_count)
    duration_txt = "définitif" if permanent else f"{minutes} min"
    log_event(db, "manual_ban", f"Bannissement manuel {duration_txt}" + (f" : {reason}" if reason else ""),
              ip, country, "manual")
    return row


def unban_ip(db, ip: str) -> bool:
    ip = normalize_ip(ip)
    row = db.query(BlockedIP).filter(BlockedIP.ip_address == ip).first()
    now = datetime.utcnow()
    if row is None or not (row.permanent or row.blocked_until > now):
        return False
    row.blocked_until = now
    row.permanent = False
    row.updated_at = now
    log_event(db, "unban", "Débloquée par l'administrateur", ip, row.country, "manual")
    return True


def is_banned(db, ip: str, country: Optional[str] = None) -> bool:
    return evaluate(load_state(db), ip, country).code == "banned"


# ── Maintenance ──────────────────────────────────────────────────────────────

def purge(db) -> None:
    settings = get_settings(db)
    now = datetime.utcnow()
    retention = timedelta(days=max(1, int(settings.retention_days or 30)))
    # Les tentatives doivent survivre à la plus longue fenêtre de règle.
    longest = max([r["window_minutes"] for r in parse_rules(settings.rules)] or [0])
    attempts_keep = max(retention, timedelta(minutes=longest) + timedelta(days=1))

    db.query(SecurityEvent).filter(SecurityEvent.created_at < now - retention).delete(synchronize_session=False)
    db.query(AuthAttempt).filter(AuthAttempt.created_at < now - attempts_keep).delete(synchronize_session=False)
    db.query(BlockedIP).filter(
        BlockedIP.permanent == False,  # noqa: E712
        BlockedIP.blocked_until < now - retention,
    ).delete(synchronize_session=False)
    db.commit()


async def maintenance_loop():
    last_purge = 0.0
    while True:
        await asyncio.sleep(FLUSH_INTERVAL)
        db = session_factory()
        try:
            flush_hits(db)
            if time.monotonic() - last_purge > PURGE_INTERVAL:
                purge(db)
                last_purge = time.monotonic()
        except Exception as exc:
            logger.error("Erreur maintenance IPBan : %s", exc)
            db.rollback()
        finally:
            db.close()
