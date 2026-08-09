# PROJECT_CONTEXT.md

Mémoire persistante du projet Sereveur-ESP-32.
À lire intégralement avant toute modification. À mettre à jour après toute modification.

---

## Vision du projet

Permettre à un utilisateur d'administrer un nombre croissant d'ESP32 sur son réseau local depuis une interface web unique, sans jamais ouvrir de port sur sa box internet, sans configuration manuelle sur Cloudflare, et en moins de 30 secondes par équipement ajouté.

---

## Architecture actuelle

### Flux réseau

```
Internet
  → Cloudflare (DNS / WAF / protection DDoS)
  → Cloudflare Tunnel (cloudflared, pas d'ouverture de port)
  → Caddy (reverse proxy, port 80 interne Docker)
      • iot.DOMAIN            → FastAPI backend (port 8000) = portail admin
      • slug.DOMAIN (protégé) → forward_auth /auth/check (backend) puis ESP32 local
      • slug.DOMAIN (public)  → ESP32 local (IP:port) directement
      • slug.DOMAIN (suspendu)→ page « service suspendu » (backend)
```

Le portail `iot.DOMAIN` et les API `/api/*` sont protégés par un middleware de
session (cookie signé HMAC). Pour les services en mode **protégé**, Caddy délègue
l'autorisation au backend via `forward_auth` → `/auth/check`, qui accepte
l'administrateur ainsi que les **utilisateurs** autorisés pour ce service et non
expirés (voir `docs/features/user-management.md`).

### Conteneurs Docker

| Conteneur    | Image                         | Rôle                                              |
|--------------|-------------------------------|---------------------------------------------------|
| `backend`    | Python 3.12-slim (custom)     | API FastAPI + fichiers statiques frontend         |
| `caddy`      | `caddy:2-alpine`              | Reverse proxy dynamique                           |
| `cloudflared`| `cloudflare/cloudflared`      | Tunnel Cloudflare                                 |
| `esp32-updater` | `docker:cli`               | Éphémère : mise à jour du serveur (voir `docs/features/server-update.md`) |

### Mise à jour du serveur

Un conteneur ne peut pas se recréer lui-même : exécuter `docker compose up -d
--build` depuis le backend le fait tuer en plein milieu de sa propre recréation
(conteneur bloqué en `Created`, 502 Cloudflare). La séquence est donc déportée
dans un conteneur éphémère `esp32-updater` (`docker:cli`), lancé via le socket
Docker, qui survit à la recréation du backend.

Le projet y est monté **au même chemin absolu que sur l'hôte**
(`HOST_PROJECT_PATH`) : Compose s'y exécute, mais les bind-mounts relatifs du
`docker-compose.yml` sont résolus par le démon de l'hôte. Un chemin différent
casserait les montages de `caddy` et `backend`.

### Volumes Docker

| Volume       | Contenu                                    |
|--------------|--------------------------------------------|
| `db_data`    | Base de données SQLite (`devices.db`)      |
| `caddy_data` | Certificats Caddy                          |
| `caddy_config` | Configuration runtime Caddy             |

### Base de données

Table `devices` (SQLite via SQLAlchemy) :

| Champ          | Type      | Contrainte         |
|----------------|-----------|--------------------|
| `id`           | INTEGER   | PK, autoincrement  |
| `project_name` | TEXT      | NOT NULL           |
| `slug`         | TEXT      | UNIQUE, NOT NULL   |
| `public_url`   | TEXT      |                    |
| `local_ip`     | TEXT      | NOT NULL           |
| `local_port`   | INTEGER   | NOT NULL, défaut 80|
| `description`  | TEXT      | défaut ""          |
| `status`       | TEXT      | défaut "unknown"   |
| `created_at`   | DATETIME  | server_default now |
| `last_seen`    | DATETIME  | nullable           |
| `access_mode`  | TEXT      | protected/suspended/public/public_temporary |
| `public_until` | DATETIME  | nullable (accès public temporaire) |
| `local_protocol` | TEXT    | http/https         |

Tables complémentaires : `users` + `user_device_access` (gestion des utilisateurs à
accès limité, voir `docs/features/user-management.md`), `auth_attempts`, `blocked_ips`,
`access_logs` (authentification et journalisation).

### Gestion Caddy (dynamique)

Au démarrage du backend et à chaque modification d'équipement :
- `POST http://caddy:2019/load` avec la config JSON complète
- Config inclut : route principale (`iot.DOMAIN` → `backend:8000`) + une route par équipement
- Caddy applique la config atomiquement sans interruption de service

### Gestion DNS Cloudflare

À chaque ajout d'équipement :
- Création d'un enregistrement CNAME : `slug.DOMAIN` → `<TUNNEL_ID>.cfargotunnel.com` (proxied)

À chaque suppression :
- Recherche de l'enregistrement DNS et suppression via API Cloudflare

### Frontend

Servi statiquement par FastAPI (`app.mount("/", StaticFiles(directory="frontend"))`).
Aucune étape de build, aucune dépendance npm.

| Fichier              | Rôle                                                              |
|----------------------|-------------------------------------------------------------------|
| `index.html`         | Portail : barre translucide, contrôle segmenté, 3 vues, feuilles  |
| `login.html`         | Page de connexion                                                  |
| `css/style.css`      | Système visuel complet (jetons, thèmes, composants, accessibilité) |
| `js/motion.js`       | Moteur d'interaction (ressorts, feuilles, gestes, menus, thème)    |
| `js/app.js`          | Équipements, outils, navigation, notifications                     |
| `js/users.js`        | Utilisateurs secondaires                                           |

Le système visuel suit les principes de design d'Apple :

- **Ressorts plutôt que durées.** `motion.js` résout analytiquement un ressort
  paramétré en *amortissement* (dépassement) et *réponse* (rapidité, en secondes),
  et non en masse/raideur/frottement. Toute animation repart de la valeur affichée
  et de la vélocité en cours : elle est interruptible et réversible sans saut.
- **Gestes 1:1.** Les feuilles modales suivent le doigt exactement, résistent
  progressivement au-delà de leur position de repos, et décident de se fermer
  d'après le point d'arrêt *projeté* par l'élan (`v/1000 · d/(1−d)`, d = 0.998),
  pas d'après la position au relâchement.
- **Matériaux et profondeur.** Barre, menus et notifications sont des couches
  translucides (`backdrop-filter`) sous lesquelles le contenu défile ; la
  séparation n'apparaît que lorsqu'elles recouvrent réellement du contenu.
- **Typographie.** Police système, tracking et interlignage définis par taille
  (négatif sur les grands titres, neutre sur le corps), espacements en `rem` pour
  suivre la taille de texte choisie par l'utilisateur.
- **Accessibilité.** `prefers-reduced-motion` (translations et rebonds remplacés
  par des fondus), `prefers-reduced-transparency` (matériaux opaques),
  `prefers-contrast` (bordures franches), focus visible, piège de focus dans les
  feuilles, statut jamais porté par la couleur seule.

Thème clair/sombre automatique (`prefers-color-scheme`), avec sélecteur manuel
persisté dans `localStorage` sous la clé `sereveur-theme` et appliqué avant le
premier rendu pour éviter tout flash.

### Surveillance des équipements

Tâche asyncio en arrière-plan (`monitor.py`) :
- Intervalle : 60 secondes
- Méthode : requête HTTP GET sur `http://local_ip:port`
- Seuil "slow" : > 3 secondes de réponse
- Mise à jour : `status` + `last_seen` en base

---

## Choix techniques validés

| Décision                         | Justification                                              |
|----------------------------------|------------------------------------------------------------|
| FastAPI                          | Spécifié dans le cahier des charges, async natif           |
| SQLite                           | Spécifié, suffisant pour des dizaines d'équipements        |
| Caddy admin API JSON             | Reconfiguration dynamique sans rechargement de fichier     |
| `POST /load` Caddy               | Remplacement atomique de toute la config, plus fiable      |
| Vanilla JS (pas de framework)    | Simplicité, pas de dépendances de build                    |
| Ressorts en JS (`motion.js`)     | Animations interruptibles et sensibles à la vélocité, impossibles avec des transitions CSS |
| Python 3.12                      | Version LTS récente, support asyncio complet               |
| cloudflared via token d'env      | Méthode moderne, pas de fichier de config à gérer          |
| Slugs réservés (iot, api, www…)  | Éviter les conflits avec le portail principal              |

---

## Fonctionnalités terminées

- [x] Structure du projet (backend / frontend / caddy / cloudflared / docs / scripts)
- [x] Modèle de données SQLAlchemy (`Device`)
- [x] Schémas Pydantic avec validation (slug, IP, port, slugs réservés)
- [x] API CRUD complète (`/api/devices/`)
- [x] Endpoint de rafraîchissement manuel (`/api/devices/{id}/refresh`)
- [x] Endpoint de santé (`/api/health`)
- [x] Service Cloudflare (création/suppression DNS CNAME)
- [x] Service Caddy (synchronisation config dynamique via admin API)
- [x] Service monitor (surveillance périodique en arrière-plan)
- [x] Frontend : tableau de bord avec cartes équipements
- [x] Frontend : bouton "Ouvrir" (lien vers URL publique de l'ESP32)
- [x] Frontend : modal ajout/modification
- [x] Frontend : indicateurs de statut (vert/orange/rouge)
- [x] Frontend : auto-refresh toutes les 30 secondes
- [x] Docker Compose (backend + caddy + cloudflared)
- [x] Scripts shell (install, start, stop, backup, restore, check-health)
- [x] Tests pytest (schémas, CRUD, config Caddy, monitor)
- [x] `pytest.ini` + `requirements-dev.txt`
- [x] `cloudflared/config.yml` (méthode fichier, alternative au token)
- [x] Documentation README (installation, scripts, tests, backup, dépannage)
- [x] Documentation PROJECT_CONTEXT.md
- [x] Documentation par fonctionnalité (docs/features/)
- [x] Guides de configuration étape par étape (docs/setup/)
- [x] CHANGELOG.md
- [x] Modes d'accès par service (protégé / public temporaire / public / suspendu)
- [x] Authentification locale (login/mot de passe admin, protection anti-bruteforce)
- [x] Gestion des utilisateurs à accès limité (par service + date de validité)
- [x] Interface en onglets (Équipements / Utilisateurs / Outils)
- [x] Refonte de l'interface selon les principes de design d'Apple (thème clair/sombre,
      matériaux translucides, animations à ressorts interruptibles, accessibilité)

---

## Fonctionnalités en cours

Aucune.

---

## Fonctionnalités prévues

- [ ] Authentification Cloudflare Access (prioritaire)
- [ ] MQTT broker intégré
- [ ] Intégration Home Assistant
- [ ] Intégration Node-RED
- [ ] Intégration Grafana + InfluxDB
- [ ] Monitoring des données capteurs (historisation)
- [ ] Gestion OTA des ESP32 (upload firmware)
- [ ] Versioning des firmwares
- [ ] Groupement des équipements par projet/site
- [ ] Notifications (email, Telegram) sur changement de statut
- [ ] API WebSocket pour les mises à jour temps réel du dashboard

---

## Contraintes

- Aucun port ne doit être ouvert sur la box internet
- Tout le trafic passe obligatoirement par Cloudflare Tunnel
- Aucune configuration manuelle sur Cloudflare pour chaque équipement
- Support de plusieurs dizaines d'équipements sans modification structurelle
- Ajout d'un équipement en moins de 30 secondes
- Architecture simple : pas de frameworks frontend, pas de sur-ingénierie
- SQLite uniquement (pas de PostgreSQL, pas de Redis)
- Une seule version du projet dans le dépôt (pas de v1/v2/backup)

---

## Dette technique connue

- La configuration du tunnel Cloudflare (ingress rules) est manuelle via le dashboard Cloudflare ; une automatisation via l'API Cloudflare Tunnel serait préférable
- Le DNS pour `iot.DOMAIN` (portail principal) doit être créé manuellement ; il pourrait être automatisé au premier démarrage
- Pas de gestion des erreurs de rate-limit Cloudflare API (429)
- Le conteneur `esp32-updater` est lancé avec accès au socket Docker : quiconque
  atteint `/api/system/update` pilote le démon de l'hôte. L'endpoint est protégé
  par la session administrateur, mais c'est une élévation de privilèges par
  conception, au même titre que le terminal serveur
- La surveillance utilise HTTP GET ; certains ESP32 pourraient ne pas avoir de route GET sur `/`
- Pas de pagination sur l'API `/api/devices/` (à ajouter si > 100 équipements)

---

## Historique des décisions

| Date       | Décision                                                                                      |
|------------|-----------------------------------------------------------------------------------------------|
| 2026-06-13 | Démarrage du projet sur base du cahier des charges reçu                                       |
| 2026-06-13 | Choix de Caddy (vs Traefik) : API admin JSON plus simple pour la gestion dynamique           |
| 2026-06-13 | Choix de `POST /load` (vs `PATCH /config/`) pour remplacer la config Caddy atomiquement      |
| 2026-06-13 | Frontend vanilla JS : aucune dépendance de build, maintenabilité maximale                     |
| 2026-06-13 | cloudflared géré via token d'env (méthode moderne, recommandée par Cloudflare depuis 2022)    |
| 2026-06-13 | Surveillance HTTP plutôt que ping ICMP : plus représentative de la disponibilité réelle       |
| 2026-06-13 | Ajout des scripts shell, tests pytest, guides setup, cloudflared/config.yml                   |
| 2026-06-13 | Renommage `SECRET_KEY` → `APP_SECRET_KEY` dans .env.example pour cohérence avec le cahier    |
| 2026-07-25 | Gestion des utilisateurs : accès limité par service + date de validité, rôles dans le jeton |
| 2026-07-25 | Jeton de session enrichi d'un rôle (`admin`/`user`), compat. ascendante via `parse_token`   |
| 2026-07-25 | Interface réorganisée en onglets (Équipements / Utilisateurs / Outils) pour désencombrer    |
| 2026-08-08 | Refonte de l'interface selon les principes de design d'Apple, sans dépendance ni build      |
| 2026-08-08 | Moteur de ressorts maison (`motion.js`) plutôt qu'une bibliothèque : ~40 lignes utiles, zéro dépendance |
| 2026-08-08 | Actions secondaires des cartes déplacées dans un menu ancré : chemin courant visible d'abord |
| 2026-08-09 | Mise à jour déportée dans un conteneur éphémère : un conteneur ne peut pas se recréer lui-même |
| 2026-08-09 | Projet monté dans l'updater au chemin identique à l'hôte : les bind-mounts sont résolus par le démon hôte |
