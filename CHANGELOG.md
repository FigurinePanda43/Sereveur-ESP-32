# CHANGELOG

## [2.2.0] - 2026-08-08

### Refonte de l'interface web selon les principes de design d'Apple

**Fichiers créés :**
- `frontend/js/motion.js` — Moteur d'interaction : solveur de ressort analytique
  paramétré en (amortissement, réponse), présentation des feuilles modales avec
  glisser-pour-fermer, projection d'élan, résistance élastique, contrôle segmenté
  à pouce glissant, menus ancrés, gestion du thème

**Fichiers modifiés :**
- `frontend/css/style.css` — Système visuel reconstruit : palette système claire et
  sombre, matériaux translucides (`backdrop-filter`), typographie à tracking et
  interlignage variables selon la taille, espacements en `rem`, prise en charge de
  `prefers-reduced-motion`, `prefers-reduced-transparency` et `prefers-contrast`
- `frontend/index.html` — Structure revue : barre translucide, contrôle segmenté,
  feuilles modales, listes d'options pour les modes d'accès, rangées d'outils
- `frontend/login.html` — Même système visuel, thème clair/sombre
- `frontend/js/app.js` — Cartes redessinées (action principale + menu d'actions
  secondaires), feuilles animées par ressort, notification à ressort, sélecteur
  d'apparence, révélation séquencée des cartes
- `frontend/js/users.js` — Mêmes cartes et feuilles pour les utilisateurs
- `backend/routers/auth.py` — Pages autonomes « service suspendu » et « accès refusé »
  regroupées dans un gabarit commun, au même système visuel

**Impact :** Aucun changement fonctionnel ni d'API. Toutes les fonctionnalités
existantes sont conservées à l'identique (équipements, utilisateurs, modes d'accès,
scan réseau, terminal, mise à jour). L'interface s'adapte désormais au thème clair
ou sombre du système, avec un sélecteur manuel, et respecte les préférences
d'accessibilité du système d'exploitation.

**Risque :** Faible. Modifications limitées au frontend et à deux pages HTML
statiques du backend. Les identifiants DOM et les appels API sont inchangés.

**Instructions de migration :** Aucune. Vider le cache du navigateur si l'ancienne
feuille de style persiste (les URL sont versionnées : `style.css?v=20`).

---

## [2.1.0] - 2026-07-25

### Fonctionnalité : Gestion des utilisateurs (accès limité par service et par date)

**Fichiers créés :**
- `backend/routers/users.py` — API CRUD des utilisateurs (`/api/users/`)
- `frontend/js/users.js` — Interface de gestion des utilisateurs
- `backend/tests/test_users.py` — Tests (schémas, CRUD, contrôle d'accès, cascade)
- `backend/tests/conftest.py` — Rend `pytest tests/` exécutable en local et en conteneur
- `docs/features/user-management.md` — Documentation de la fonctionnalité

**Fichiers modifiés :**
- `backend/models.py` — Nouveaux modèles `User` et `UserDeviceAccess`
- `backend/schemas.py` — Schémas `UserCreate`, `UserUpdate`, `UserResponse`
- `backend/auth.py` — Jetons de session avec rôle (`admin`/`user`), `parse_token`, hash de mot de passe utilisateur
- `backend/routers/auth.py` — Connexion des utilisateurs + contrôle `forward_auth` par service/validité
- `backend/routers/devices.py` — Suppression en cascade des accès lors de la suppression d'un service
- `backend/main.py` — Enregistrement du routeur utilisateurs
- `frontend/index.html` — Navigation par onglets (Équipements / Utilisateurs / Outils) + fenêtres utilisateurs
- `frontend/js/app.js` — Routage des vues
- `frontend/css/style.css` — Styles navigation, outils, cartes utilisateurs
- `frontend/login.html` — Messages d'erreur « compte expiré / désactivé »
- `backend/tests/test_devices.py` — Mise à jour de la suite existante (format Caddyfile
  texte au lieu de l'ancienne config JSON, authentification des appels API, tests des
  modes d'accès)

**Impact :** Un utilisateur créé par l'administrateur accède uniquement aux services
qui lui sont attribués (mode Protégé), jusqu'à sa date de validité (ou sans limite).

**Risque :** Faible à modéré. Le format du jeton de session évolue ; les sessions
administrateur existantes restent valides (compatibilité ascendante gérée par
`parse_token`).

**Instructions de migration :** Aucune. Les tables `users` et `user_device_access`
sont créées automatiquement au démarrage. Aucune nouvelle variable d'environnement.

---

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
