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
  → FastAPI backend (port 8000, interne Docker) pour iot.DOMAIN
  → ESP32 local (IP:port) pour slug.DOMAIN
```

### Conteneurs Docker

| Conteneur    | Image                         | Rôle                                              |
|--------------|-------------------------------|---------------------------------------------------|
| `backend`    | Python 3.12-slim (custom)     | API FastAPI + fichiers statiques frontend         |
| `caddy`      | `caddy:2-alpine`              | Reverse proxy dynamique                           |
| `cloudflared`| `cloudflare/cloudflared`      | Tunnel Cloudflare                                 |

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
| Python 3.12                      | Version LTS récente, support asyncio complet               |
| cloudflared via token d'env      | Méthode moderne, pas de fichier de config à gérer          |
| Slugs réservés (iot, api, www…)  | Éviter les conflits avec le portail principal              |

---

## Fonctionnalités terminées

- [x] Structure du projet (backend / frontend / caddy / docs)
- [x] Modèle de données SQLAlchemy (`Device`)
- [x] Schémas Pydantic avec validation (slug, IP, port)
- [x] API CRUD complète (`/api/devices/`)
- [x] Endpoint de rafraîchissement manuel (`/api/devices/{id}/refresh`)
- [x] Endpoint de santé (`/api/health`)
- [x] Service Cloudflare (création/suppression DNS CNAME)
- [x] Service Caddy (synchronisation config dynamique via admin API)
- [x] Service monitor (surveillance périodique en arrière-plan)
- [x] Frontend : tableau de bord avec cartes équipements
- [x] Frontend : modal ajout/modification
- [x] Frontend : indicateurs de statut (vert/orange/rouge)
- [x] Frontend : auto-refresh toutes les 30 secondes
- [x] Docker Compose (backend + caddy + cloudflared)
- [x] Documentation README
- [x] Documentation PROJECT_CONTEXT.md
- [x] Documentation par fonctionnalité (docs/features/)
- [x] CHANGELOG.md

---

## Fonctionnalités en cours

Aucune.

---

## Fonctionnalités prévues

- [ ] Authentification Cloudflare Access (prioritaire)
- [ ] Authentification locale (login/mot de passe) comme alternative
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
