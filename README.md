# Sereveur-ESP-32

Plateforme centralisée d'administration et d'accès sécurisé aux équipements ESP32 via internet.

## Vue d'ensemble

L'application expose une interface web unique (`https://iot.DOMAIN`) permettant d'enregistrer, surveiller et accéder à n'importe quel ESP32 du réseau local via un sous-domaine HTTPS automatiquement créé.

```
Internet → Cloudflare → Cloudflare Tunnel → Caddy (reverse proxy) → ESP32 local
```

Aucun port ne doit être ouvert sur la box internet. Aucune configuration manuelle sur Cloudflare.

---

## Fonctionnalités

- Portail d'administration : tableau de bord de tous les équipements
- Ajout/modification/suppression d'un ESP32 via formulaire
- Création automatique du DNS Cloudflare (CNAME vers tunnel)
- Configuration automatique du reverse proxy (Caddy admin API)
- Surveillance périodique du statut de chaque équipement (online / slow / offline)
- Architecture extensible (MQTT, Home Assistant, Grafana, OTA...)

---

## Architecture technique

| Composant         | Rôle                                              |
|-------------------|---------------------------------------------------|
| FastAPI (Python)  | Backend API + service des fichiers statiques      |
| SQLite            | Base de données locale                            |
| Caddy             | Reverse proxy dynamique (admin API JSON)          |
| cloudflared       | Tunnel Cloudflare (pas d'ouverture de port)       |
| Docker Compose    | Orchestration des conteneurs                      |

---

## Prérequis

- Serveur Linux (VM Proxmox recommandée)
- Docker + Docker Compose installés
- Nom de domaine géré par Cloudflare
- Compte Cloudflare avec accès API
- Tunnel Cloudflare créé (token disponible)

---

## Variables d'environnement

Copier `.env.example` → `.env` et renseigner les valeurs.

| Variable                  | Description                                         | Exemple                        |
|---------------------------|-----------------------------------------------------|--------------------------------|
| `DOMAIN`                  | Domaine racine                                      | `mondomaine.com`               |
| `CLOUDFLARE_API_TOKEN`    | Token API Cloudflare (permissions DNS:Edit)         | `abc123...`                    |
| `CLOUDFLARE_ZONE_ID`      | Zone ID du domaine sur Cloudflare                   | `def456...`                    |
| `CLOUDFLARE_TUNNEL_ID`    | ID du tunnel Cloudflare                             | `ghi789...`                    |
| `CLOUDFLARE_TUNNEL_TOKEN` | Token du tunnel pour cloudflared                    | `eyJ...`                       |
| `SECRET_KEY`              | Clé secrète pour les sessions (générer aléatoirement) | `openssl rand -hex 32`       |

---

## Installation et déploiement

### 1. Cloner le dépôt

```bash
git clone <url-du-depot>
cd Sereveur-ESP-32
```

### 2. Configurer l'environnement

```bash
cp .env.example .env
# Éditer .env avec les vraies valeurs
```

### 3. Créer le tunnel Cloudflare

Dans le tableau de bord Cloudflare Zero Trust :
1. Créer un tunnel nommé `esp32-manager`
2. Copier le token dans `.env` → `CLOUDFLARE_TUNNEL_TOKEN`
3. Configurer l'ingress du tunnel : `*.DOMAIN` → `http://caddy:80`
4. Copier l'ID du tunnel dans `.env` → `CLOUDFLARE_TUNNEL_ID`

### 4. Créer le DNS pour le portail principal

```bash
# Via Cloudflare dashboard ou API : créer un CNAME
# iot.DOMAIN → <TUNNEL_ID>.cfargotunnel.com (proxied)
```

### 5. Lancer l'application

```bash
docker compose up -d
```

### 6. Vérifier

```bash
docker compose ps
docker compose logs -f backend
```

Accéder à : `https://iot.DOMAIN`

---

## Ajouter un équipement ESP32

1. Ouvrir `https://iot.DOMAIN`
2. Cliquer **Ajouter un équipement**
3. Renseigner : nom, sous-domaine (slug), IP locale, port
4. Valider → DNS Cloudflare créé + route Caddy configurée automatiquement

L'équipement est accessible en moins de 30 secondes via `https://slug.DOMAIN`.

---

## Structure du projet

```
.
├── backend/                 # API FastAPI (Python)
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py              # Point d'entrée FastAPI
│   ├── database.py          # Connexion SQLite / SQLAlchemy
│   ├── models.py            # Modèle SQLAlchemy Device
│   ├── schemas.py           # Schémas Pydantic
│   ├── routers/
│   │   └── devices.py       # Routes CRUD équipements
│   └── services/
│       ├── cloudflare.py    # API Cloudflare DNS
│       ├── caddy.py         # API admin Caddy
│       └── monitor.py       # Surveillance périodique
├── frontend/                # Interface web (HTML/CSS/JS vanilla)
│   ├── index.html
│   ├── css/style.css
│   └── js/app.js
├── caddy/
│   └── Caddyfile            # Config minimale (admin API activée)
├── docs/
│   ├── features/            # Documentation par fonctionnalité
│   └── open_questions.md    # Questions en attente de validation
├── docker-compose.yml
├── .env.example
├── PROJECT_CONTEXT.md
└── CHANGELOG.md
```

---

## API Backend

| Méthode | Endpoint                    | Description                          |
|---------|-----------------------------|--------------------------------------|
| GET     | `/api/devices/`             | Liste tous les équipements           |
| POST    | `/api/devices/`             | Crée un équipement                   |
| GET     | `/api/devices/{id}`         | Détail d'un équipement               |
| PUT     | `/api/devices/{id}`         | Modifie un équipement                |
| DELETE  | `/api/devices/{id}`         | Supprime un équipement               |
| POST    | `/api/devices/{id}/refresh` | Force la vérification du statut      |
| GET     | `/api/health`               | Statut de l'API                      |

Documentation interactive : `https://iot.DOMAIN/docs`

---

## Évolutions futures

Voir `PROJECT_CONTEXT.md` section "Fonctionnalités prévues".

---

## Dépannage

**Caddy ne répond pas :**
```bash
docker compose logs caddy
```

**DNS Cloudflare non créé :**
Vérifier `CLOUDFLARE_API_TOKEN` et `CLOUDFLARE_ZONE_ID` dans `.env`.

**Équipement toujours offline :**
Vérifier que l'IP locale est joignable depuis le serveur (`ping <IP>`).
