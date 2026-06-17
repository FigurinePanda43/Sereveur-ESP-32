# Gestionnaire de publication de services locaux

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
├── cloudflared/
│   └── config.yml           # Config tunnel alternative (méthode fichier)
├── docs/
│   ├── features/            # Documentation par fonctionnalité
│   ├── setup/               # Guides de configuration étape par étape
│   └── open_questions.md    # Questions en attente de validation
├── scripts/
│   ├── install.sh           # Installation initiale
│   ├── start.sh             # Démarrage
│   ├── stop.sh              # Arrêt
│   ├── backup.sh            # Sauvegarde de la base de données
│   ├── restore.sh           # Restauration de la base de données
│   └── check-health.sh      # Vérification de l'état de santé
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

## Tests

```bash
# Depuis le répertoire backend
cd backend
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/ -v
```

Ou via Docker (sans installation locale) :

```bash
docker compose run --rm backend pip install -r requirements-dev.txt && \
docker compose run --rm backend pytest tests/ -v
```

### Couverture des tests

| Test                              | Description                                       |
|-----------------------------------|---------------------------------------------------|
| Validation du slug                | Format, majuscules, caractères spéciaux, réservés |
| Validation de l'IP locale         | Format IPv4, valeurs hors range                   |
| Validation du port                | Entier 1–65535                                    |
| Ajout d'un équipement             | Création, slug doublon, URL publique              |
| Modification d'un équipement      | Description, IP, valeurs invalides                |
| Suppression d'un équipement       | Suppression réelle, 404 si introuvable            |
| Génération config Caddy           | Structure JSON, routes, admin API                 |
| Surveillance HTTP (online)        | Réponse 200 → statut "online"                     |
| Surveillance HTTP (offline)       | ConnectError, Timeout → statut "offline"          |

---

## Scripts

| Script                  | Description                                    |
|-------------------------|------------------------------------------------|
| `scripts/install.sh`    | Installation initiale (env, images Docker)     |
| `scripts/start.sh`      | Démarrage des conteneurs                       |
| `scripts/stop.sh`       | Arrêt des conteneurs (volumes conservés)       |
| `scripts/backup.sh`     | Sauvegarde de la base SQLite dans `backups/`   |
| `scripts/restore.sh`    | Restauration depuis un fichier de sauvegarde   |
| `scripts/check-health.sh`| Vérification de santé de tous les composants  |

---

## Sauvegarde et restauration

### Sauvegarde manuelle

```bash
bash scripts/backup.sh
# → backups/devices_20260613_220000.db
```

### Lister les sauvegardes

```bash
ls -lh backups/
```

### Restaurer

```bash
bash scripts/restore.sh backups/devices_20260613_220000.db
```

Le script :
1. Crée une sauvegarde de sécurité avant restauration
2. Arrête le backend
3. Restaure le fichier
4. Redémarre le backend

### Sauvegarde automatique (recommandé)

Ajouter un cron job sur la VM :

```bash
crontab -e
# Sauvegarde quotidienne à 2h00
0 2 * * * cd /home/esp32admin/Sereveur-ESP-32 && bash scripts/backup.sh >> /var/log/esp32-backup.log 2>&1
```

---

## Guides de configuration

Documentation étape par étape dans `docs/setup/` :

1. [Création de la VM Proxmox](docs/setup/01-proxmox-vm.md)
2. [Installation de Docker](docs/setup/02-docker.md)
3. [Configuration Cloudflare](docs/setup/03-cloudflare.md)
4. [Premier lancement](docs/setup/04-first-launch.md)

---

## Évolutions futures

Voir `PROJECT_CONTEXT.md` section "Fonctionnalités prévues".

---

## Dépannage

**Vérification globale :**
```bash
bash scripts/check-health.sh
```

**Caddy ne répond pas :**
```bash
docker compose logs caddy
# Vérifier que le port 80 n'est pas utilisé par un autre processus :
sudo ss -tlnp | grep :80
```

**DNS Cloudflare non créé :**
```bash
# Vérifier les variables dans .env
grep CLOUDFLARE .env
# Vérifier les logs du backend au moment de l'ajout
docker compose logs backend --tail=20
```

**Équipement toujours offline :**
```bash
# Tester la joignabilité depuis la VM
ping -c 3 <IP_ESP32>
curl -v http://<IP_ESP32>:<PORT>
# Vérifier que l'ESP32 a bien un serveur HTTP actif
```

**Le tunnel Cloudflare ne se connecte pas :**
```bash
docker compose logs cloudflared --tail=30
# Vérifier CLOUDFLARE_TUNNEL_TOKEN dans .env
```

**Réinitialiser Caddy :**
```bash
docker compose restart caddy
# Le backend re-synchronise Caddy au prochain redémarrage
docker compose restart backend
```

**Accéder aux logs en temps réel :**
```bash
docker compose logs -f                  # Tous les services
docker compose logs -f backend          # Backend uniquement
docker compose logs -f cloudflared      # Tunnel uniquement
```
