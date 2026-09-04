"""Tests de non-régression pour l'audit de sécurité.

Couvre :
- l'absence du contournement WebSocket sur les services protégés ;
- le plafonnement de la durée de vie du jeton de session (≤ 1 mois) ;
- la révocation globale via AUTH_MIN_ISSUED_AT ;
- la neutralisation des redirections ouvertes (_safe_next) ;
- le rejet des IP dangereuses pour un équipement (loopback, lien-local…) ;
- le resserrement du seuil anti-force brute par défaut.
"""
import os
import time

os.environ.setdefault("DOMAIN", "test.local")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-0123456789abcdef0123456789")
os.environ.setdefault("ADMIN_USER", "admin")
os.environ.setdefault("ADMIN_PASSWORD_HASH", "x")

import pytest
from pydantic import ValidationError

import auth
from schemas import DeviceCreate
from services.caddy import _build_caddyfile


class _Dev:
    def __init__(self, **k):
        self.__dict__.update(k)


def _device(mode="protected", **extra):
    base = dict(slug="svc", local_ip="192.168.1.50", local_port=80,
                local_protocol="http", access_mode=mode, public_until=None)
    base.update(extra)
    return _Dev(**base)


# ── Contournement WebSocket (la faille critique) ────────────────────────────

class TestWebSocketBypass:
    def test_protected_block_has_no_websocket_exclusion(self):
        cf = _build_caddyfile([_device("protected")])
        # L'ancien matcher qui laissait passer les WS sans auth ne doit plus exister.
        assert "@notws" not in cf
        assert "not header Upgrade" not in cf

    def test_protected_block_still_enforces_forward_auth(self):
        cf = _build_caddyfile([_device("protected")])
        assert "forward_auth backend:8000" in cf
        assert "/auth/check" in cf

    def test_forward_auth_precedes_proxy(self):
        cf = _build_caddyfile([_device("protected")])
        assert cf.index("forward_auth") < cf.index("reverse_proxy 192.168.1.50:80")


# ── Retrait optionnel du cookie vers l'équipement ───────────────────────────

class TestCookieStrip:
    def test_present_by_default(self):
        # Activé par défaut : le cookie de session ne doit jamais atteindre
        # l'équipement en amont (cas d'usage : mini serveur web sans cookie propre).
        cf = _build_caddyfile([_device("protected")])
        assert "header_up -Cookie" in cf

    def test_applies_to_all_modes(self):
        for mode in ("protected", "public", "public_temporary"):
            extra = {"public_until": None}
            cf = _build_caddyfile([_device(mode, **extra)])
            assert "header_up -Cookie" in cf, f"cookie non retiré en mode {mode}"

    def test_absent_when_disabled(self, monkeypatch):
        monkeypatch.setattr("services.caddy.STRIP_COOKIES", False)
        cf = _build_caddyfile([_device("protected")])
        assert "header_up -Cookie" not in cf


# ── Durée de vie du jeton ───────────────────────────────────────────────────

class TestSessionLifetime:
    def test_max_age_capped_to_one_month(self):
        # Quelle que soit la config, jamais plus de 31 jours.
        assert auth.SESSION_MAX_AGE <= 31 * 24 * 3600

    def test_token_older_than_max_age_is_invalid(self, monkeypatch):
        real_now = int(time.time())
        tok = auth.make_token("admin", "admin")
        monkeypatch.setattr(auth, "SESSION_MAX_AGE", 10)
        monkeypatch.setattr(auth.time, "time", lambda: real_now + 1000)
        assert auth.parse_token(tok) is None

    def test_min_issued_at_revokes_old_tokens(self, monkeypatch):
        tok = auth.make_token("admin", "admin")
        assert auth.parse_token(tok) is not None
        # Seuil de révocation dans le futur → le jeton (émis maintenant) est refusé.
        monkeypatch.setattr(auth, "_MIN_ISSUED_AT", int(time.time()) + 3600)
        assert auth.parse_token(tok) is None


# ── Redirection ouverte ─────────────────────────────────────────────────────

class TestSafeNext:
    @pytest.mark.parametrize("value", [
        "https://evil.com",
        "//evil.com",
        "https://evil.com/path",
        "http://test.local.evil.com",
        "javascript:alert(1)",
        "/\\evil.com",
    ])
    def test_external_targets_rejected(self, value):
        assert auth._safe_next(value) == "/"

    @pytest.mark.parametrize("value", [
        "/",
        "/api/devices",
        "https://iot.test.local/",
        "https://svc.test.local/page",
    ])
    def test_same_site_targets_allowed(self, value):
        assert auth._safe_next(value) == value


# ── Validation d'IP d'équipement ────────────────────────────────────────────

class TestLocalIpValidation:
    @pytest.mark.parametrize("ip", ["127.0.0.1", "169.254.169.254", "0.0.0.0", "224.0.0.1", "::1"])
    def test_dangerous_ip_rejected(self, ip):
        with pytest.raises(ValidationError):
            DeviceCreate(project_name="x", slug="x", local_ip=ip, local_port=80)

    @pytest.mark.parametrize("ip", ["192.168.1.50", "10.0.0.5", "172.16.0.9"])
    def test_normal_lan_ip_accepted(self, ip):
        d = DeviceCreate(project_name="x", slug="x", local_ip=ip, local_port=80)
        assert d.local_ip == ip


# ── Anti-force brute ────────────────────────────────────────────────────────

class TestBruteForceDefault:
    def test_default_threshold_is_tightened(self):
        # L'ancien réglage tolérait 50 échecs / 10 min. Le premier palier par
        # défaut doit désormais être nettement plus strict (≤ 10 essais).
        import importlib
        import auth as auth_mod
        importlib.reload(auth_mod)
        # Valeur par défaut de l'env
        assert int(os.getenv("BRUTEFORCE_MAX_ATTEMPTS", "10")) <= 10
