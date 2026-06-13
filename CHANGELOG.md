# CHANGELOG

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
