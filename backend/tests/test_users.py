"""
Tests de la gestion des utilisateurs secondaires (accès limité par service et
par date de validité).

Lancement :
    cd backend
    pip install -r requirements.txt -r requirements-dev.txt
    pytest tests/test_users.py -v
"""
import os

import bcrypt

# Variables d'environnement obligatoires avant tout import de l'application
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("DOMAIN", "test.local")
os.environ.setdefault("CADDY_ADMIN_URL", "http://mock-caddy:2019")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-0123456789abcdef0123456789")
os.environ.setdefault("ADMIN_USER", "admin")
os.environ.setdefault(
    "ADMIN_PASSWORD_HASH",
    bcrypt.hashpw(b"adminpw", bcrypt.gensalt()).decode(),
)

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401  (enregistre toutes les tables sur Base.metadata)
from database import Base, get_db

# StaticPool → une seule connexion in-memory partagée entre tous les threads,
# indispensable car les endpoints synchrones tournent dans un threadpool.
_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSession = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
Base.metadata.create_all(bind=_engine)


def _override_get_db():
    db = _TestSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="module")
def client():
    with (
        patch("services.caddy.sync_caddy", new_callable=AsyncMock, return_value=True),
        patch("services.cloudflare.create_dns_record", new_callable=AsyncMock, return_value=True),
        patch("services.cloudflare.delete_dns_record", new_callable=AsyncMock, return_value=True),
        patch("services.monitor.monitor_loop", new_callable=AsyncMock),
        patch("services.access_expiry.expiry_loop", new_callable=AsyncMock),
        patch("main._wait_for_caddy", new_callable=AsyncMock, return_value=True),
    ):
        from fastapi.testclient import TestClient
        from main import app

        app.dependency_overrides[get_db] = _override_get_db
        with TestClient(app) as c:
            yield c
        app.dependency_overrides.clear()


# ── Helpers d'authentification ────────────────────────────────────────────────

def _admin_headers():
    import auth
    return {"Cookie": f"{auth.COOKIE_NAME}={auth.make_token('admin', 'admin')}"}


def _user_headers(username):
    import auth
    return {"Cookie": f"{auth.COOKIE_NAME}={auth.make_token(username, 'user')}"}


def _make_device(client, slug, name="Service"):
    resp = client.post(
        "/api/devices/",
        json={"project_name": name, "slug": slug, "local_ip": "192.168.1.10"},
        headers=_admin_headers(),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ── Validation des schémas ────────────────────────────────────────────────────

from pydantic import ValidationError
from schemas import UserCreate


class TestUserSchemaValidation:
    def test_username_valide(self):
        u = UserCreate(username="jean.dupont", password="secret123")
        assert u.username == "jean.dupont"

    def test_username_trop_court(self):
        with pytest.raises(ValidationError):
            UserCreate(username="ab", password="secret123")

    def test_username_caractere_invalide(self):
        with pytest.raises(ValidationError, match="identifiant"):
            UserCreate(username="jean dupont", password="secret123")

    def test_username_deux_points_interdit(self):
        with pytest.raises(ValidationError):
            UserCreate(username="jean:dupont", password="secret123")

    def test_mot_de_passe_trop_court(self):
        with pytest.raises(ValidationError):
            UserCreate(username="jean", password="123")


# ── CRUD utilisateurs ─────────────────────────────────────────────────────────

class TestUserCrud:
    def test_liste_vide(self, client):
        resp = client.get("/api/users/", headers=_admin_headers())
        assert resp.status_code == 200
        assert resp.json() == []

    def test_creation(self, client):
        did = _make_device(client, "crud-cuve")
        resp = client.post(
            "/api/users/",
            json={"username": "alice", "password": "secret123", "device_ids": [did]},
            headers=_admin_headers(),
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["username"] == "alice"
        assert data["enabled"] is True
        assert data["expired"] is False
        assert [d["slug"] for d in data["devices"]] == ["crud-cuve"]

    def test_creation_doublon(self, client):
        resp = client.post(
            "/api/users/",
            json={"username": "alice", "password": "secret123", "device_ids": []},
            headers=_admin_headers(),
        )
        assert resp.status_code == 409

    def test_creation_identifiant_admin_refuse(self, client):
        resp = client.post(
            "/api/users/",
            json={"username": "admin", "password": "secret123", "device_ids": []},
            headers=_admin_headers(),
        )
        assert resp.status_code == 409

    def test_creation_service_inexistant(self, client):
        resp = client.post(
            "/api/users/",
            json={"username": "bob", "password": "secret123", "device_ids": [99999]},
            headers=_admin_headers(),
        )
        assert resp.status_code == 400

    def test_mot_de_passe_trop_court_rejete(self, client):
        resp = client.post(
            "/api/users/",
            json={"username": "charlie", "password": "123", "device_ids": []},
            headers=_admin_headers(),
        )
        assert resp.status_code == 422

    def test_modification_expiration_et_desactivation(self, client):
        uid = client.post(
            "/api/users/",
            json={"username": "dora", "password": "secret123", "device_ids": []},
            headers=_admin_headers(),
        ).json()["id"]

        resp = client.put(
            f"/api/users/{uid}",
            json={"valid_until": "2000-01-01T00:00:00Z"},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["expired"] is True

        resp = client.put(f"/api/users/{uid}", json={"valid_until": None}, headers=_admin_headers())
        assert resp.json()["valid_until"] is None
        assert resp.json()["expired"] is False

        resp = client.put(f"/api/users/{uid}", json={"enabled": False}, headers=_admin_headers())
        assert resp.json()["enabled"] is False

    def test_suppression(self, client):
        uid = client.post(
            "/api/users/",
            json={"username": "erin", "password": "secret123", "device_ids": []},
            headers=_admin_headers(),
        ).json()["id"]
        assert client.delete(f"/api/users/{uid}", headers=_admin_headers()).status_code == 204
        assert client.get(f"/api/users/{uid}", headers=_admin_headers()).status_code == 404


# ── Contrôle d'accès forward_auth ─────────────────────────────────────────────

class TestForwardAuth:
    def test_utilisateur_acces_service_autorise(self, client):
        did = _make_device(client, "fa-ok")
        client.post(
            "/api/users/",
            json={"username": "fred", "password": "secret123", "device_ids": [did]},
            headers=_admin_headers(),
        )
        resp = client.get(
            "/auth/check",
            headers={**_user_headers("fred"), "x-forwarded-host": "fa-ok.test.local"},
            follow_redirects=False,
        )
        assert resp.status_code == 200

    def test_utilisateur_service_non_autorise(self, client):
        _make_device(client, "fa-secret")
        client.post(
            "/api/users/",
            json={"username": "gina", "password": "secret123", "device_ids": []},
            headers=_admin_headers(),
        )
        resp = client.get(
            "/auth/check",
            headers={**_user_headers("gina"), "x-forwarded-host": "fa-secret.test.local"},
            follow_redirects=False,
        )
        assert resp.status_code == 403

    def test_utilisateur_expire_refuse(self, client):
        did = _make_device(client, "fa-exp")
        uid = client.post(
            "/api/users/",
            json={"username": "hugo", "password": "secret123", "device_ids": [did]},
            headers=_admin_headers(),
        ).json()["id"]
        client.put(
            f"/api/users/{uid}",
            json={"valid_until": "2000-01-01T00:00:00Z"},
            headers=_admin_headers(),
        )
        resp = client.get(
            "/auth/check",
            headers={**_user_headers("hugo"), "x-forwarded-host": "fa-exp.test.local"},
            follow_redirects=False,
        )
        assert resp.status_code == 403

    def test_admin_acces_tous_services(self, client):
        _make_device(client, "fa-admin")
        resp = client.get(
            "/auth/check",
            headers={**_admin_headers(), "x-forwarded-host": "fa-admin.test.local"},
            follow_redirects=False,
        )
        assert resp.status_code == 200

    def test_non_authentifie_redirige(self, client):
        resp = client.get(
            "/auth/check",
            headers={"x-forwarded-host": "fa-ok.test.local"},
            follow_redirects=False,
        )
        assert resp.status_code == 302

    def test_utilisateur_ne_peut_pas_acceder_api_admin(self, client):
        client.post(
            "/api/users/",
            json={"username": "ivan", "password": "secret123", "device_ids": []},
            headers=_admin_headers(),
        )
        resp = client.get("/api/users/", headers=_user_headers("ivan"), follow_redirects=False)
        assert resp.status_code == 401


# ── Cascade suppression service → accès ──────────────────────────────────────

class TestCascade:
    def test_suppression_service_retire_acces(self, client):
        did = _make_device(client, "cascade-svc")
        uid = client.post(
            "/api/users/",
            json={"username": "jade", "password": "secret123", "device_ids": [did]},
            headers=_admin_headers(),
        ).json()["id"]

        assert client.delete(f"/api/devices/{did}", headers=_admin_headers()).status_code == 204

        resp = client.get(f"/api/users/{uid}", headers=_admin_headers())
        assert resp.status_code == 200
        assert resp.json()["devices"] == []
