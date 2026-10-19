"""
Tests IPBan : décision de filtrage, anti-force brute (paliers, récidive),
listes blanche/noire, filtrage par pays, protection anti-verrouillage de
l'administrateur, compte-rendu et journal.

Lancement :
    cd backend
    pytest tests/test_ipban.py -v
"""
import os

import bcrypt

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("DOMAIN", "test.local")
os.environ.setdefault("CADDY_ADMIN_URL", "http://mock-caddy:2019")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-0123456789abcdef0123456789")
os.environ.setdefault("ADMIN_USER", "admin")
os.environ.setdefault(
    "ADMIN_PASSWORD_HASH",
    bcrypt.hashpw(b"adminpw", bcrypt.gensalt()).decode(),
)

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
import ipaddress

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
from database import Base, get_db
from services import ipban

_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
_TestSession = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_IP = "81.250.10.7"
ATTACKER_IP = "45.155.205.5"


def _override_get_db():
    db = _TestSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="module")
def client():
    previous = ipban.session_factory
    ipban.session_factory = _TestSession
    with (
        patch("services.caddy.sync_caddy", new_callable=AsyncMock, return_value=True) as sync,
        patch("services.cloudflare.create_dns_record", new_callable=AsyncMock, return_value=True),
        patch("services.cloudflare.delete_dns_record", new_callable=AsyncMock, return_value=True),
        patch("services.monitor.monitor_loop", new_callable=AsyncMock),
        patch("services.access_expiry.expiry_loop", new_callable=AsyncMock),
        patch("services.ipban.maintenance_loop", new_callable=AsyncMock),
        patch("main._wait_for_caddy", new_callable=AsyncMock, return_value=True),
        # bcrypt (≈ 0,25 s par essai) rendrait chaque rafale d'échecs très lente.
        patch("routers.auth.verify_password", side_effect=lambda pw: pw == "adminpw"),
    ):
        from fastapi.testclient import TestClient
        from main import app

        app.dependency_overrides[get_db] = _override_get_db
        with TestClient(app) as c:
            c.sync_caddy = sync
            yield c
        app.dependency_overrides.clear()
    ipban.session_factory = previous
    ipban.invalidate()


@pytest.fixture(autouse=True)
def fresh_db():
    """Base vide et réglages par défaut avant chaque test."""
    Base.metadata.drop_all(bind=_engine)
    Base.metadata.create_all(bind=_engine)
    ipban._hits.clear()
    ipban.invalidate()
    yield
    ipban.invalidate()


def _admin(ip=ADMIN_IP, country="FR"):
    import auth
    headers = {"Cookie": f"{auth.COOKIE_NAME}={auth.make_token('admin', 'admin')}", "CF-Connecting-IP": ip}
    if country:
        headers["CF-IPCountry"] = country
    return headers


def _from(ip, country=None, **extra):
    headers = {"CF-Connecting-IP": ip, **extra}
    if country:
        headers["CF-IPCountry"] = country
    return headers


def _fail_login(client, ip=ATTACKER_IP, times=1, country=None):
    for _ in range(times):
        client.post(
            "/auth/login",
            data={"username": "root", "password": "nope"},
            headers=_from(ip, country),
            follow_redirects=False,
        )


def _put(client, **settings):
    return client.put("/api/security/settings", json=settings, headers=_admin())


# ── Décision (fonction pure) ─────────────────────────────────────────────────

def _state(**k):
    base = dict(enabled=True)
    base.update(k)
    return ipban.State(**base)


def _net(v):
    return (ipaddress.ip_network(v, strict=False), "")


class TestEvaluate:
    def test_disabled_allows_everything(self):
        st = _state(enabled=False, mode="whitelist")
        assert ipban.evaluate(st, ATTACKER_IP).allowed

    def test_forced_off_allows_everything(self):
        st = _state(forced_off=True, mode="whitelist")
        assert ipban.evaluate(st, ATTACKER_IP).code == "disabled"

    def test_standard_allows_unknown_visitor(self):
        assert ipban.evaluate(_state(), ATTACKER_IP).code == "allowed"

    def test_whitelist_mode_blocks_others(self):
        st = _state(mode="whitelist", allow=[_net("81.250.10.0/24")])
        assert ipban.evaluate(st, "81.250.10.42").code == "whitelisted"
        assert ipban.evaluate(st, ATTACKER_IP).code == "not_whitelisted"

    def test_whitelist_beats_ban_and_country(self):
        st = _state(
            allow=[_net(ATTACKER_IP)],
            bans={ATTACKER_IP: ipban.Ban(datetime.utcnow() + timedelta(hours=1), False, "x")},
            country_mode="block", countries=frozenset({"CN"}),
        )
        assert ipban.evaluate(st, ATTACKER_IP, "CN").allowed

    def test_ban_and_expiry(self):
        future = datetime.utcnow() + timedelta(minutes=5)
        st = _state(bans={ATTACKER_IP: ipban.Ban(future, False, "10 échecs")})
        d = ipban.evaluate(st, ATTACKER_IP)
        assert d.code == "banned" and d.until == future
        later = future + timedelta(seconds=1)
        assert ipban.evaluate(st, ATTACKER_IP, now=later).allowed

    def test_denylist_cidr(self):
        st = _state(deny=[_net("45.155.205.0/24")])
        assert ipban.evaluate(st, "45.155.205.200").code == "denylist"
        assert ipban.evaluate(st, "45.155.206.1").allowed

    def test_private_exemption(self):
        st = _state(mode="whitelist")
        assert ipban.evaluate(st, "192.168.1.20").code == "private"
        st.exempt_private = False
        assert ipban.evaluate(st, "192.168.1.20").code == "not_whitelisted"

    def test_country_allow_mode(self):
        st = _state(country_mode="allow", countries=frozenset({"FR", "BE"}))
        assert ipban.evaluate(st, ATTACKER_IP, "FR").allowed
        assert ipban.evaluate(st, ATTACKER_IP, "US").code == "country_not_allowed"
        # Pays inconnu (en-tête absent) : refusé en mode « seulement ».
        assert ipban.evaluate(st, ATTACKER_IP, None).code == "country_not_allowed"

    def test_country_block_mode(self):
        st = _state(country_mode="block", countries=frozenset({"CN", "T1"}))
        assert ipban.evaluate(st, ATTACKER_IP, "cn").code == "country_blocked"
        assert ipban.evaluate(st, ATTACKER_IP, "T1").code == "country_blocked"
        assert ipban.evaluate(st, ATTACKER_IP, "FR").allowed

    def test_services_scope_can_be_excluded(self):
        st = _state(mode="whitelist", apply_to_services=False)
        assert ipban.evaluate(st, ATTACKER_IP, scope="service").code == "out_of_scope"
        assert not ipban.evaluate(st, ATTACKER_IP, scope="portal").allowed

    def test_ipv4_mapped_ipv6_is_normalized(self):
        st = _state(deny=[_net(ATTACKER_IP)])
        assert ipban.evaluate(st, f"::ffff:{ATTACKER_IP}").code == "denylist"

    def test_unparseable_ip_is_not_blocked(self):
        assert ipban.evaluate(_state(mode="whitelist"), "testclient").code == "unknown_ip"


# ── Anti-force brute ─────────────────────────────────────────────────────────

class TestBruteForce:
    def test_ban_after_threshold(self, client):
        _fail_login(client, times=9)
        assert client.get("/auth/login", headers=_from(ATTACKER_IP)).status_code == 200
        _fail_login(client, times=1)
        resp = client.get("/auth/login", headers=_from(ATTACKER_IP))
        assert resp.status_code == 403
        assert "bloqué" in resp.text

        bans = client.get("/api/security/bans", headers=_admin()).json()
        assert [b["ip"] for b in bans] == [ATTACKER_IP]
        assert bans[0]["source"] == "auto"
        assert bans[0]["ban_count"] == 1

    def test_banned_ip_blocked_on_api_with_json(self, client):
        _fail_login(client, times=10)
        resp = client.get("/api/devices/", headers=_admin(ip=ATTACKER_IP))
        assert resp.status_code == 403
        assert resp.json()["reason"] == "banned"

    def test_other_ip_unaffected(self, client):
        _fail_login(client, times=10)
        assert client.get("/auth/login", headers=_from("45.155.205.99")).status_code == 200

    def test_unban_restores_access_and_resets_counter(self, client):
        _fail_login(client, times=10)
        resp = client.delete(f"/api/security/bans/{ATTACKER_IP}", headers=_admin())
        assert resp.status_code == 204
        assert client.get("/auth/login", headers=_from(ATTACKER_IP)).status_code == 200
        # Les échecs déjà sanctionnés ne comptent plus : un échec ne rebannit pas.
        _fail_login(client, times=1)
        assert client.get("/auth/login", headers=_from(ATTACKER_IP)).status_code == 200

    def test_repeat_offender_gets_longer_ban(self, client):
        _fail_login(client, times=10)
        first = client.get("/api/security/bans", headers=_admin()).json()[0]
        client.delete(f"/api/security/bans/{ATTACKER_IP}", headers=_admin())
        _fail_login(client, times=10)
        second = client.get("/api/security/bans", headers=_admin()).json()[0]
        assert second["ban_count"] == 2
        assert "récidive" in second["reason"]
        d1 = datetime.fromisoformat(first["blocked_until"]) - datetime.fromisoformat(first["since"])
        d2 = datetime.fromisoformat(second["blocked_until"]) - datetime.fromisoformat(second["since"])
        assert d2 >= d1 * 1.9

    def test_permanent_after_n_bans(self, client):
        assert _put(client, permanent_after=2).status_code == 200
        _fail_login(client, times=10)
        client.delete(f"/api/security/bans/{ATTACKER_IP}", headers=_admin())
        _fail_login(client, times=10)
        ban = client.get("/api/security/bans", headers=_admin()).json()[0]
        assert ban["permanent"] is True
        assert ban["blocked_until"] is None

    def test_custom_rules(self, client):
        resp = _put(client, rules=[{"window_minutes": 5, "max_failures": 3, "ban_minutes": 60}])
        assert resp.status_code == 200, resp.text
        _fail_login(client, times=3)
        assert client.get("/auth/login", headers=_from(ATTACKER_IP)).status_code == 403

    def test_bruteforce_can_be_disabled(self, client):
        _put(client, bruteforce_enabled=False)
        _fail_login(client, times=12)
        assert client.get("/auth/login", headers=_from(ATTACKER_IP)).status_code == 200

    def test_ipban_disabled_blocks_nothing(self, client):
        _fail_login(client, times=10)
        assert _put(client, enabled=False).status_code == 200
        assert client.get("/auth/login", headers=_from(ATTACKER_IP)).status_code == 200

    def test_whitelisted_ip_never_banned(self, client):
        client.post("/api/security/lists", json={"list_type": "allow", "value": ATTACKER_IP}, headers=_admin())
        _fail_login(client, times=15)
        assert client.get("/auth/login", headers=_from(ATTACKER_IP)).status_code == 200
        assert client.get("/api/security/bans", headers=_admin()).json() == []

    def test_rule_validation(self, client):
        resp = _put(client, rules=[{"window_minutes": 0, "max_failures": 3, "ban_minutes": 60}])
        assert resp.status_code == 422
        resp = _put(client, rules=[])
        assert resp.status_code == 400


# ── Bannissement manuel ──────────────────────────────────────────────────────

class TestManualBan:
    def test_manual_ban_and_list(self, client):
        resp = client.post("/api/security/bans", json={"ip": ATTACKER_IP, "duration_minutes": 60, "reason": "scan"},
                           headers=_admin())
        assert resp.status_code == 201, resp.text
        assert resp.json()["source"] == "manual"
        assert client.get("/", headers=_from(ATTACKER_IP)).status_code == 403

    def test_permanent_manual_ban(self, client):
        resp = client.post("/api/security/bans", json={"ip": ATTACKER_IP, "duration_minutes": None}, headers=_admin())
        assert resp.json()["permanent"] is True

    def test_cannot_ban_self(self, client):
        resp = client.post("/api/security/bans", json={"ip": ADMIN_IP}, headers=_admin())
        assert resp.status_code == 409

    def test_cannot_ban_exempt_private_ip(self, client):
        resp = client.post("/api/security/bans", json={"ip": "192.168.1.30"}, headers=_admin())
        assert resp.status_code == 409

    def test_invalid_ip_rejected(self, client):
        resp = client.post("/api/security/bans", json={"ip": "pas-une-ip"}, headers=_admin())
        assert resp.status_code == 422

    def test_unban_unknown_ip(self, client):
        assert client.delete("/api/security/bans/45.155.205.77", headers=_admin()).status_code == 404


# ── Listes et modes ──────────────────────────────────────────────────────────

class TestListsAndModes:
    def test_whitelist_mode_requires_admin_in_list(self, client):
        resp = _put(client, mode="whitelist")
        assert resp.status_code == 409
        assert ADMIN_IP in resp.json()["detail"]
        # Rien n'a été enregistré.
        assert client.get("/api/security/settings", headers=_admin()).json()["settings"]["mode"] == "standard"

    def test_whitelist_mode(self, client):
        r = client.post("/api/security/lists", json={"list_type": "allow", "value": "81.250.10.0/24", "note": "Maison"},
                        headers=_admin())
        assert r.status_code == 201, r.text
        assert _put(client, mode="whitelist").status_code == 200
        assert client.get("/auth/login", headers=_from("81.250.10.200")).status_code == 200
        assert client.get("/auth/login", headers=_from(ATTACKER_IP)).status_code == 403

    def test_cannot_remove_own_whitelist_entry(self, client):
        rule = client.post("/api/security/lists", json={"list_type": "allow", "value": ADMIN_IP}, headers=_admin()).json()
        _put(client, mode="whitelist")
        assert client.delete(f"/api/security/lists/{rule['id']}", headers=_admin()).status_code == 409
        assert len(client.get("/api/security/lists", headers=_admin()).json()["allow"]) == 1

    def test_denylist(self, client):
        r = client.post("/api/security/lists", json={"list_type": "deny", "value": "45.155.205.0/24"}, headers=_admin())
        assert r.json()["value"] == "45.155.205.0/24"
        assert client.get("/auth/login", headers=_from(ATTACKER_IP)).status_code == 403

    def test_denylist_covering_admin_refused(self, client):
        r = client.post("/api/security/lists", json={"list_type": "deny", "value": "81.250.10.0/24"}, headers=_admin())
        assert r.status_code == 409
        assert client.get("/api/security/lists", headers=_admin()).json()["deny"] == []

    def test_duplicate_entry(self, client):
        client.post("/api/security/lists", json={"list_type": "deny", "value": "45.155.205.5/32"}, headers=_admin())
        r = client.post("/api/security/lists", json={"list_type": "deny", "value": ATTACKER_IP}, headers=_admin())
        assert r.status_code == 409

    def test_invalid_network(self, client):
        r = client.post("/api/security/lists", json={"list_type": "deny", "value": "300.1.1.1"}, headers=_admin())
        assert r.status_code == 422

    def test_country_allow(self, client):
        assert _put(client, country_mode="allow", countries=["fr", "BE"]).status_code == 200
        assert client.get("/auth/login", headers=_from(ATTACKER_IP, "BE")).status_code == 200
        assert client.get("/auth/login", headers=_from(ATTACKER_IP, "US")).status_code == 403

    def test_country_allow_requires_admin_country(self, client):
        resp = client.put("/api/security/settings", json={"country_mode": "allow", "countries": ["BE"]},
                          headers=_admin(country="FR"))
        assert resp.status_code == 409

    def test_country_allow_requires_a_country(self, client):
        assert _put(client, country_mode="allow", countries=[]).status_code == 400

    def test_country_block(self, client):
        assert _put(client, country_mode="block", countries=["CN"]).status_code == 200
        assert client.get("/auth/login", headers=_from(ATTACKER_IP, "CN")).status_code == 403
        assert client.get("/auth/login", headers=_from(ATTACKER_IP, "US")).status_code == 200

    def test_invalid_country_code(self, client):
        assert _put(client, countries=["France"]).status_code == 422

    def test_settings_report_client(self, client):
        data = client.get("/api/security/settings", headers=_admin()).json()
        assert data["client"] == {"ip": ADMIN_IP, "country": "FR", "allowed": True, "code": "allowed",
                                  "label": "Autorisée"}
        assert data["settings"]["enabled"] is True
        assert len(data["settings"]["rules"]) == 3

    def test_test_endpoint(self, client):
        client.post("/api/security/lists", json={"list_type": "deny", "value": "45.155.205.0/24"}, headers=_admin())
        r = client.post("/api/security/test", json={"ip": ATTACKER_IP}, headers=_admin()).json()
        assert r["allowed"] is False and r["code"] == "denylist"


# ── Services (forward_auth) ──────────────────────────────────────────────────

class TestServices:
    def test_protected_service_check_blocked(self, client):
        client.post("/api/security/bans", json={"ip": ATTACKER_IP}, headers=_admin())
        resp = client.get("/auth/check", headers=_from(ATTACKER_IP, **{"X-Forwarded-Host": "cuve.test.local"}),
                          follow_redirects=False)
        assert resp.status_code == 403

    def test_public_service_check(self, client):
        assert client.get("/ipban/check", headers=_from(ATTACKER_IP, **{"X-Forwarded-Host": "cuve.test.local"})).status_code == 200
        client.post("/api/security/bans", json={"ip": ATTACKER_IP}, headers=_admin())
        assert client.get("/ipban/check", headers=_from(ATTACKER_IP, **{"X-Forwarded-Host": "cuve.test.local"})).status_code == 403

    def test_services_out_of_scope(self, client):
        client.post("/api/security/bans", json={"ip": ATTACKER_IP}, headers=_admin())
        _put(client, apply_to_services=False)
        svc = _from(ATTACKER_IP, **{"X-Forwarded-Host": "cuve.test.local"})
        assert client.get("/ipban/check", headers=svc).status_code == 200
        # Le portail reste protégé.
        assert client.get("/auth/login", headers=_from(ATTACKER_IP)).status_code == 403

    def test_forged_forwarded_host_does_not_escape_portal_scope(self, client):
        client.post("/api/security/bans", json={"ip": ATTACKER_IP}, headers=_admin())
        _put(client, apply_to_services=False)
        forged = _from(ATTACKER_IP, **{"X-Forwarded-Host": "cuve.test.local"})
        assert client.get("/auth/login", headers=forged).status_code == 403

    def test_caddy_resynced_when_guard_changes(self, client):
        client.sync_caddy.reset_mock()
        _put(client, retention_days=10)
        assert client.sync_caddy.await_count == 0
        _put(client, apply_to_services=False)
        assert client.sync_caddy.await_count == 1

    def test_caddyfile_guard(self):
        from services.caddy import _build_caddyfile

        class D:
            def __init__(self, slug, mode):
                self.slug, self.access_mode, self.public_until = slug, mode, None
                self.local_ip, self.local_port, self.local_protocol = "192.168.1.5", 80, "http"

        devices = [D("pub", "public"), D("prot", "protected")]
        guarded = _build_caddyfile(devices, guard_public=True)
        assert guarded.count("uri /ipban/check") == 1
        assert guarded.count("uri /auth/check") == 1
        assert "/ipban/check" not in _build_caddyfile(devices, guard_public=False)


# ── Compte-rendu et journal ──────────────────────────────────────────────────

class TestReport:
    def test_report_counts(self, client):
        _fail_login(client, times=10, country="CN")
        for _ in range(5):
            client.get("/auth/login", headers=_from(ATTACKER_IP, "CN"))
        rep = client.get("/api/security/report?period=24h", headers=_admin()).json()
        t = rep["totals"]
        assert t["failed_logins"] == 10
        assert t["blocked_requests"] == 5
        assert t["bans"] == 1
        assert t["active_bans"] == 1
        assert t["unique_attackers"] == 1
        assert rep["top_ips"][0] == {"ip": ATTACKER_IP, "failed": 10, "blocked": 5, "country": "CN", "banned": True}
        assert rep["top_countries"][0] == {"country": "CN", "count": 15}
        assert rep["top_usernames"][0] == {"username": "root", "count": 10}
        assert rep["block_reasons"][0]["code"] == "banned"
        assert len(rep["timeline"]) == 24
        assert sum(b["failed"] for b in rep["timeline"]) == 10
        assert sum(b["blocked"] for b in rep["timeline"]) == 5

    def test_blocked_requests_are_aggregated(self, client):
        client.post("/api/security/bans", json={"ip": ATTACKER_IP}, headers=_admin())
        for _ in range(20):
            client.get("/", headers=_from(ATTACKER_IP))
        ev = client.get("/api/security/events?kind=blocked", headers=_admin()).json()["items"]
        assert len(ev) == 1 and ev[0]["count"] == 20

    def test_report_periods(self, client):
        for period, n in (("7d", 7), ("30d", 30)):
            rep = client.get(f"/api/security/report?period={period}", headers=_admin()).json()
            assert len(rep["timeline"]) == n
        assert client.get("/api/security/report?period=1y", headers=_admin()).status_code == 422

    def test_events_journal(self, client):
        _fail_login(client, times=10)
        items = client.get("/api/security/events", headers=_admin()).json()["items"]
        kinds = {i["kind"] for i in items}
        assert {"auth_failed", "ban"} <= kinds
        only = client.get("/api/security/events?kind=auth_failed&limit=4", headers=_admin()).json()
        assert len(only["items"]) == 4 and only["next_before"]
        assert all(i["kind"] == "auth_failed" for i in only["items"])

    def test_settings_change_logged(self, client):
        _put(client, enabled=False)
        items = client.get("/api/security/events?kind=admin", headers=_admin()).json()["items"]
        assert items[0]["message"] == "IPBan désactivé"

    def test_security_api_requires_admin(self, client):
        assert client.get("/api/security/settings").status_code == 401


# ── Maintenance et levier de secours ─────────────────────────────────────────

class TestMaintenance:
    def test_purge_old_events(self):
        db = _TestSession()
        try:
            old = datetime.utcnow() - timedelta(days=60)
            db.add(models.SecurityEvent(event_type="blocked", ip_address=ATTACKER_IP, created_at=old, last_seen=old))
            db.add(models.SecurityEvent(event_type="blocked", ip_address=ATTACKER_IP))
            db.commit()
            ipban.purge(db)
            assert db.query(models.SecurityEvent).count() == 1
        finally:
            db.close()

    def test_forced_off_env(self, client, monkeypatch):
        client.post("/api/security/bans", json={"ip": ATTACKER_IP}, headers=_admin())
        monkeypatch.setenv("IPBAN_DISABLED", "true")
        ipban.invalidate()
        assert client.get("/auth/login", headers=_from(ATTACKER_IP)).status_code == 200
        assert client.get("/api/security/settings", headers=_admin()).json()["settings"]["forced_off"] is True
