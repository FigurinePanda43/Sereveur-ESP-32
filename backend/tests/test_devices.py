"""
Tests de la plateforme ESP32 Manager.

Lancement :
    cd backend
    pip install -r requirements.txt -r requirements-dev.txt
    pytest tests/ -v

Ou via Docker :
    docker compose run --rm backend pytest tests/ -v
"""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Variables d'environnement obligatoires avant tout import de l'application
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("DOMAIN", "test.local")
os.environ.setdefault("CADDY_ADMIN_URL", "http://mock-caddy:2019")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, get_db

# Base de données en mémoire pour les tests (isolée, ne touche pas /data/)
_test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
_TestSession = sessionmaker(autocommit=False, autoflush=False, bind=_test_engine)
Base.metadata.create_all(bind=_test_engine)


def _override_get_db():
    db = _TestSession()
    try:
        yield db
    finally:
        db.close()


# ── Fixture principale ────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    """Client de test FastAPI avec services externes mockés."""
    with (
        patch("services.caddy.sync_caddy", new_callable=AsyncMock, return_value=True),
        patch("services.cloudflare.create_dns_record", new_callable=AsyncMock, return_value=True),
        patch("services.cloudflare.delete_dns_record", new_callable=AsyncMock, return_value=True),
        patch("services.monitor.monitor_loop", new_callable=AsyncMock),
        patch("main._wait_for_caddy", new_callable=AsyncMock, return_value=True),
    ):
        from fastapi.testclient import TestClient
        from main import app

        app.dependency_overrides[get_db] = _override_get_db
        with TestClient(app) as c:
            yield c
        app.dependency_overrides.clear()


# ── Tests de validation des schémas Pydantic ─────────────────────────────────

from pydantic import ValidationError
from schemas import DeviceCreate


class TestSlugValidation:
    def test_slug_valide(self):
        d = DeviceCreate(project_name="Test", slug="cuve-go", local_ip="192.168.1.1")
        assert d.slug == "cuve-go"

    def test_slug_chiffres_valide(self):
        d = DeviceCreate(project_name="Test", slug="esp32-01", local_ip="192.168.1.1")
        assert d.slug == "esp32-01"

    def test_slug_majuscules_invalide(self):
        with pytest.raises(ValidationError, match="minuscules"):
            DeviceCreate(project_name="Test", slug="CuvGo", local_ip="192.168.1.1")

    def test_slug_espace_invalide(self):
        with pytest.raises(ValidationError, match="minuscules"):
            DeviceCreate(project_name="Test", slug="ma cuve", local_ip="192.168.1.1")

    def test_slug_caractere_special_invalide(self):
        with pytest.raises(ValidationError, match="minuscules"):
            DeviceCreate(project_name="Test", slug="cuve_go!", local_ip="192.168.1.1")

    def test_slug_reserve_iot(self):
        with pytest.raises(ValidationError, match="réservé"):
            DeviceCreate(project_name="Test", slug="iot", local_ip="192.168.1.1")

    def test_slug_reserve_api(self):
        with pytest.raises(ValidationError, match="réservé"):
            DeviceCreate(project_name="Test", slug="api", local_ip="192.168.1.1")

    def test_slug_reserve_www(self):
        with pytest.raises(ValidationError, match="réservé"):
            DeviceCreate(project_name="Test", slug="www", local_ip="192.168.1.1")


class TestIpValidation:
    def test_ip_valide_classe_c(self):
        d = DeviceCreate(project_name="T", slug="dev", local_ip="192.168.1.45")
        assert d.local_ip == "192.168.1.45"

    def test_ip_valide_classe_a(self):
        d = DeviceCreate(project_name="T", slug="dev2", local_ip="10.0.0.1")
        assert d.local_ip == "10.0.0.1"

    def test_ip_invalide_texte(self):
        with pytest.raises(ValidationError, match="IP invalide"):
            DeviceCreate(project_name="T", slug="dev3", local_ip="pas.une.ip")

    def test_ip_invalide_octet_hors_range(self):
        with pytest.raises(ValidationError, match="IP invalide"):
            DeviceCreate(project_name="T", slug="dev4", local_ip="192.168.1.999")

    def test_ip_invalide_vide(self):
        with pytest.raises(ValidationError):
            DeviceCreate(project_name="T", slug="dev5", local_ip="")


class TestPortValidation:
    def test_port_valide_80(self):
        d = DeviceCreate(project_name="T", slug="dev6", local_ip="192.168.1.1", local_port=80)
        assert d.local_port == 80

    def test_port_valide_8080(self):
        d = DeviceCreate(project_name="T", slug="dev7", local_ip="192.168.1.1", local_port=8080)
        assert d.local_port == 8080

    def test_port_zero_invalide(self):
        with pytest.raises(ValidationError):
            DeviceCreate(project_name="T", slug="dev8", local_ip="192.168.1.1", local_port=0)

    def test_port_trop_grand_invalide(self):
        with pytest.raises(ValidationError):
            DeviceCreate(project_name="T", slug="dev9", local_ip="192.168.1.1", local_port=99999)


# ── Tests des endpoints CRUD ──────────────────────────────────────────────────

class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestListDevices:
    def test_liste_vide_initiale(self, client):
        resp = client.get("/api/devices/")
        assert resp.status_code == 200
        assert resp.json() == []


class TestCreateDevice:
    def test_creation_valide(self, client):
        payload = {
            "project_name": "Cuve GO Marseille",
            "slug": "cuve-go",
            "local_ip": "192.168.1.45",
            "local_port": 80,
            "description": "Capteur de niveau",
        }
        resp = client.post("/api/devices/", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["slug"] == "cuve-go"
        assert data["public_url"] == "https://cuve-go.test.local"
        assert data["status"] == "unknown"
        assert data["local_ip"] == "192.168.1.45"
        assert data["local_port"] == 80

    def test_creation_slug_en_doublon(self, client):
        payload = {
            "project_name": "Autre projet",
            "slug": "cuve-go",
            "local_ip": "192.168.1.46",
        }
        resp = client.post("/api/devices/", json=payload)
        assert resp.status_code == 409

    def test_creation_ip_invalide_rejetee(self, client):
        payload = {
            "project_name": "Test",
            "slug": "test-bad-ip",
            "local_ip": "999.999.999.999",
        }
        resp = client.post("/api/devices/", json=payload)
        assert resp.status_code == 422

    def test_creation_slug_invalide_rejete(self, client):
        payload = {
            "project_name": "Test",
            "slug": "mon équipement",
            "local_ip": "192.168.1.1",
        }
        resp = client.post("/api/devices/", json=payload)
        assert resp.status_code == 422

    def test_liste_contient_equipement_cree(self, client):
        resp = client.get("/api/devices/")
        assert resp.status_code == 200
        slugs = [d["slug"] for d in resp.json()]
        assert "cuve-go" in slugs


class TestGetDevice:
    def test_get_existant(self, client):
        resp = client.get("/api/devices/1")
        assert resp.status_code == 200
        assert resp.json()["id"] == 1

    def test_get_introuvable(self, client):
        resp = client.get("/api/devices/9999")
        assert resp.status_code == 404


class TestUpdateDevice:
    def test_mise_a_jour_description(self, client):
        resp = client.put("/api/devices/1", json={"description": "Mise à jour test"})
        assert resp.status_code == 200
        assert resp.json()["description"] == "Mise à jour test"

    def test_mise_a_jour_ip(self, client):
        resp = client.put("/api/devices/1", json={"local_ip": "192.168.1.99"})
        assert resp.status_code == 200
        assert resp.json()["local_ip"] == "192.168.1.99"

    def test_mise_a_jour_ip_invalide(self, client):
        resp = client.put("/api/devices/1", json={"local_ip": "pas-une-ip"})
        assert resp.status_code == 422

    def test_mise_a_jour_introuvable(self, client):
        resp = client.put("/api/devices/9999", json={"description": "x"})
        assert resp.status_code == 404


class TestDeleteDevice:
    def test_suppression(self, client):
        # Créer un équipement dédié à la suppression
        payload = {"project_name": "À supprimer", "slug": "to-delete", "local_ip": "10.0.0.99"}
        create_resp = client.post("/api/devices/", json=payload)
        assert create_resp.status_code == 201
        device_id = create_resp.json()["id"]

        resp = client.delete(f"/api/devices/{device_id}")
        assert resp.status_code == 204

        # Vérifier la suppression
        assert client.get(f"/api/devices/{device_id}").status_code == 404

    def test_suppression_introuvable(self, client):
        resp = client.delete("/api/devices/9999")
        assert resp.status_code == 404


# ── Tests de la génération de configuration Caddy ────────────────────────────

from services.caddy import _build_config


class TestCaddyConfigBuilder:
    def test_config_sans_equipement(self):
        config = _build_config([])
        routes = config["apps"]["http"]["servers"]["main"]["routes"]
        assert len(routes) == 1
        # Route principale uniquement
        assert routes[0]["match"][0]["host"][0] == "iot.test.local"

    def test_config_avec_un_equipement(self):
        device = MagicMock()
        device.slug = "pompe"
        device.local_ip = "192.168.1.10"
        device.local_port = 8080

        config = _build_config([device])
        routes = config["apps"]["http"]["servers"]["main"]["routes"]

        assert len(routes) == 2
        device_route = routes[1]
        assert device_route["match"][0]["host"][0] == "pompe.test.local"
        assert device_route["handle"][0]["upstreams"][0]["dial"] == "192.168.1.10:8080"

    def test_config_avec_plusieurs_equipements(self):
        devices = []
        for i in range(5):
            d = MagicMock()
            d.slug = f"device-{i}"
            d.local_ip = f"192.168.1.{10 + i}"
            d.local_port = 80
            devices.append(d)

        config = _build_config(devices)
        routes = config["apps"]["http"]["servers"]["main"]["routes"]

        # 1 route principale + 5 équipements
        assert len(routes) == 6

    def test_config_inclut_admin_caddy(self):
        config = _build_config([])
        assert "admin" in config
        assert config["admin"]["listen"] == "0.0.0.0:2019"

    def test_config_ecoute_port_80(self):
        config = _build_config([])
        listen = config["apps"]["http"]["servers"]["main"]["listen"]
        assert ":80" in listen


# ── Tests de la surveillance HTTP ─────────────────────────────────────────────

class TestMonitor:
    async def test_equipement_en_ligne(self):
        import httpx
        from services.monitor import check_device

        db = MagicMock()
        device = MagicMock()
        device.id = 42
        device.local_ip = "192.168.1.45"
        device.local_port = 80

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("services.monitor.httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_instance.get.return_value = mock_response

            status = await check_device(db, device)

        assert status in ("online", "slow")
        db.query.assert_called_once()

    async def test_equipement_hors_ligne(self):
        import httpx
        from services.monitor import check_device

        db = MagicMock()
        device = MagicMock()
        device.id = 43
        device.local_ip = "192.168.99.99"
        device.local_port = 80

        with patch("services.monitor.httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_instance.get.side_effect = httpx.ConnectError("Connection refused")

            status = await check_device(db, device)

        assert status == "offline"

    async def test_equipement_timeout(self):
        import httpx
        from services.monitor import check_device

        db = MagicMock()
        device = MagicMock()
        device.id = 44
        device.local_ip = "192.168.1.200"
        device.local_port = 80

        with patch("services.monitor.httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_instance.get.side_effect = httpx.TimeoutException("Timeout")

            status = await check_device(db, device)

        assert status == "offline"
