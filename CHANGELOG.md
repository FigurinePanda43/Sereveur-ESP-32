# CHANGELOG

## [1.1.0] - 2026-06-13

### Fonctionnalité : Scripts, tests, guides de configuration

**Fichiers créés :**
- `scripts/install.sh` — Installation initiale (env, génération clé, images Docker)
- `scripts/start.sh` — Démarrage des conteneurs
- `scripts/stop.sh` — Arrêt des conteneurs
- `scripts/backup.sh` — Sauvegarde SQLite horodatée dans `backups/`
- `scripts/restore.sh` — Restauration avec sauvegarde de sécurité automatique
- `scripts/check-health.sh` — Vérification de santé (conteneurs + API + Caddy + tunnel)
- `backend/tests/__init__.py` — Package de tests
- `backend/tests/test_devices.py` — Tests pytest (schémas, CRUD, Caddy, monitor)
- `backend/requirements-dev.txt` — Dépendances de test (pytest, pytest-asyncio)
- `backend/pytest.ini` — Configuration pytest (asyncio_mode=auto)
- `cloudflared/config.yml` — Config tunnel alternative (méthode fichier de credentials)
- `docs/setup/01-proxmox-vm.md` — Guide VM Proxmox
- `docs/setup/02-docker.md` — Guide installation Docker
- `docs/setup/03-cloudflare.md` — Guide configuration Cloudflare
- `docs/setup/04-first-launch.md` — Guide premier lancement

**Fichiers modifiés :**
- `README.md` — Ajout sections Tests, Scripts, Sauvegarde, Guides, Dépannage étendu
- `.env.example` — Renommage `SECRET_KEY` → `APP_SECRET_KEY`, ajout `DATABASE_URL`
- `PROJECT_CONTEXT.md` — Mise à jour fonctionnalités terminées + historique

**Impact :** Complément du projet sans modification du code existant.

**Risque :** Faible — ajouts uniquement, aucune modification du comportement runtime.

**Instructions de migration :** Si un `.env` existait déjà avec `SECRET_KEY`, le renommer en `APP_SECRET_KEY`.

---

## [1.0.0] - 2026-06-13

### Fonctionnalité : Initialisation complète de la plateforme

**Fichiers créés :**
- `README.md` — Documentation complète du projet
- `PROJECT_CONTEXT.md` — Mémoire persistante du projet
- `CHANGELOG.md` — Ce fichier
- `.env.example` — Variables d'environnement requises
- `docker-compose.yml` — Orchestration Docker (backend + caddy + cloudflared)
- `caddy/Caddyfile` — Configuration minimale Caddy (admin API)
- `backend/Dockerfile` — Image Python 3.12
- `backend/requirements.txt` — Dépendances Python
- `backend/main.py` — Application FastAPI (lifespan, routes, static files)
- `backend/database.py` — Connexion SQLite via SQLAlchemy
- `backend/models.py` — Modèle Device
- `backend/schemas.py` — Schémas Pydantic (validation slug, IP, port)
- `backend/routers/devices.py` — Routes CRUD + refresh + health
- `backend/services/cloudflare.py` — Gestion DNS Cloudflare via API
- `backend/services/caddy.py` — Synchronisation config Caddy via admin API
- `backend/services/monitor.py` — Surveillance périodique des équipements
- `frontend/index.html` — Interface web principale
- `frontend/css/style.css` — Feuille de style
- `frontend/js/app.js` — Logique frontend (fetch API, rendu, formulaires)
- `docs/features/device-registration.md`
- `docs/features/cloudflare-management.md`
- `docs/features/reverse-proxy.md`
- `docs/features/authentication.md`
- `docs/features/monitoring.md`
- `docs/open_questions.md`

**Impact :** Création de la plateforme complète depuis zéro.

**Risque :** Faible — premier déploiement.
