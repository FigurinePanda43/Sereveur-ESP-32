# Étape 4 — Premier lancement

## Prérequis

- [Étape 1](./01-proxmox-vm.md) : VM créée et accessible
- [Étape 2](./02-docker.md) : Docker installé, projet cloné
- [Étape 3](./03-cloudflare.md) : Cloudflare configuré, tunnel créé

---

## 4.1 — Configurer l'environnement

Dans le répertoire du projet :

```bash
cd Sereveur-ESP-32

# Lancer le script d'installation (configure .env + génère la clé secrète)
bash scripts/install.sh
```

Le script crée `.env` depuis `.env.example` et génère `APP_SECRET_KEY` automatiquement.

Éditer `.env` avec les vraies valeurs :

```bash
nano .env
```

Renseigner :

```env
DOMAIN=mondomaine.com
CLOUDFLARE_API_TOKEN=<votre-token>
CLOUDFLARE_ZONE_ID=<votre-zone-id>
CLOUDFLARE_TUNNEL_ID=<votre-tunnel-id>
CLOUDFLARE_TUNNEL_TOKEN=<votre-tunnel-token>
```

Sauvegarder (`Ctrl+O`, `Entrée`, `Ctrl+X`).

---

## 4.2 — Lancer l'application

```bash
bash scripts/start.sh
```

Ou directement :

```bash
docker compose up -d
```

---

## 4.3 — Vérifier le démarrage

```bash
# Statut des conteneurs (tous doivent être "Up")
docker compose ps

# Logs du backend (chercher "Application startup complete")
docker compose logs backend --tail=30

# Logs Caddy
docker compose logs caddy --tail=20

# Logs du tunnel
docker compose logs cloudflared --tail=20
```

Résultat attendu dans les logs backend :
```
INFO     uvicorn.error — Application startup complete.
INFO     main — Caddy disponible après 1 tentative(s)
INFO     services.caddy — Caddy synchronisé (0 équipement(s))
```

---

## 4.4 — Accéder au portail

Ouvrir dans un navigateur :

```
https://iot.mondomaine.com
```

Le tableau de bord s'affiche, vide pour l'instant.

---

## 4.5 — Ajouter le premier ESP32 de test

1. Cliquer **+ Ajouter un équipement**
2. Renseigner le formulaire :

| Champ       | Exemple              |
|-------------|----------------------|
| Nom         | `Test ESP32`         |
| Sous-domaine| `test-esp32`         |
| IP locale   | `192.168.1.100`      |
| Port        | `80`                 |

3. Cliquer **Enregistrer**

Le système crée automatiquement :
- L'enregistrement en base de données
- Le CNAME DNS sur Cloudflare : `test-esp32.mondomaine.com`
- La route dans le reverse proxy Caddy

4. Vérifier dans les logs :
```bash
docker compose logs backend --tail=10
```

Résultat attendu :
```
INFO services.cloudflare — DNS créé : test-esp32.mondomaine.com → <tunnel-id>.cfargotunnel.com
INFO services.caddy — Caddy synchronisé (1 équipement(s))
```

5. Attendre 30 secondes que le DNS se propage, puis tester :
```
https://test-esp32.mondomaine.com
```

---

## 4.6 — Vérification de santé complète

```bash
bash scripts/check-health.sh
```

---

## 4.7 — Activer Cloudflare Access (recommandé)

Pour protéger le portail contre l'accès public :

1. Aller sur https://one.dash.cloudflare.com
2. **Access** → **Applications** → **Add an Application**
3. Choisir **Self-hosted**
4. Nom : `ESP32 Manager`
5. Application domain : `iot.mondomaine.com` et `*.mondomaine.com`
6. Politique : autoriser votre email uniquement

> Sans Cloudflare Access, le portail est accessible publiquement. Ne pas utiliser en production sans cette étape.

---

## Commandes utiles

```bash
# Arrêter
bash scripts/stop.sh

# Voir les logs en temps réel
docker compose logs -f

# Sauvegarde manuelle
bash scripts/backup.sh

# Vérification de santé
bash scripts/check-health.sh

# Lancer les tests
cd backend && pip install -r requirements-dev.txt && pytest tests/ -v
```

---

## Résultat final attendu

- `https://iot.mondomaine.com` : portail accessible
- Ajout d'un ESP32 en < 30 secondes
- Accès à l'interface ESP32 via `https://slug.mondomaine.com`
- Aucune configuration manuelle après le premier lancement
