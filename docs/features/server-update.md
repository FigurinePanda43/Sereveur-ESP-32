# Mise à jour du serveur

Récupère la dernière version depuis GitHub et reconstruit la pile Docker, depuis
l'onglet **Outils** du portail.

## Pourquoi un conteneur dédié

Un conteneur ne peut pas se recréer lui-même de façon fiable.

L'implémentation initiale exécutait `docker compose up -d --build` **depuis
l'intérieur** du conteneur backend. Au moment où Compose recrée le service
`backend`, Docker arrête ce conteneur : le processus `docker compose`, enfant
d'uvicorn, reçoit un SIGKILL en plein milieu de la recréation. Le nouveau
conteneur reste bloqué à l'état `Created` et ne démarre jamais — l'application
devient injoignable (Cloudflare **502 Bad Gateway**).

La séquence est donc déportée dans un conteneur **éphémère et distinct**,
`esp32-updater`, lancé via le socket Docker déjà monté. Vivant hors de la pile
qu'il modifie, il survit à la recréation du backend et va jusqu'au bout seul.

```
backend  ──(docker run -d)──►  esp32-updater  ──(socket)──►  démon Docker hôte
   │                                 │                              │
   │  streame docker logs -f         │  git pull                    │  recrée
   │                                 │  docker compose up -d --build│  backend,
   ▼                                 ▼                              ▼  caddy…
 client                         va au bout                    backend neuf
   (le flux se coupe quand le backend redémarre — c'est attendu)
```

## Le piège du chemin

Le projet est monté dans l'updater **au même chemin absolu que sur l'hôte** :

```
-v /root/Sereveur-ESP-32:/root/Sereveur-ESP-32 -w /root/Sereveur-ESP-32
```

Ce n'est pas cosmétique. Compose s'exécute dans l'updater, mais les bind-mounts
du `docker-compose.yml` (`./frontend`, `./caddy/Caddyfile`) sont résolus par le
**démon de l'hôte**, pas par l'updater. Monter le projet dans un `/project`
arbitraire ferait pointer ces montages vers un chemin inexistant côté hôte :
Caddy démarrerait sans Caddyfile et le backend sans frontend.

Ce chemin identique aligne au passage le **nom de projet Compose** — déduit du
nom du répertoire de travail — sur celui de la pile déjà en place, évitant la
création d'une seconde pile parallèle.

Il est configurable via `HOST_PROJECT_PATH` (`.env`), pour les installations
hors de `/root/Sereveur-ESP-32`.

## Dépôt privé et clé SSH

Le dépôt étant privé en `git@github.com`, `git` a besoin de la clé de
déploiement de l'hôte. `HOST_SSH_DIR` (défaut `/root/.ssh`) est monté **en
lecture seule** dans deux conteneurs :

| Conteneur       | Usage                                                       |
|-----------------|-------------------------------------------------------------|
| `backend`       | `git fetch` de `/api/system/update-check` (badge de mise à jour) |
| `esp32-updater` | `git pull origin main`                                       |

L'image backend embarque `openssh-client` à cette fin. Le `known_hosts` de
l'hôte est repris tel quel : la vérification de la clé d'hôte GitHub reste
active, elle n'est pas contournée.

## Verrou anti-double-clic

Avant de lancer, l'endpoint refuse si un conteneur `esp32-updater` tourne déjà.
L'unicité du nom de conteneur côté démon fournit de surcroît un verrou
atomique : deux requêtes simultanées ne peuvent pas démarrer deux mises à jour,
la seconde échoue proprement et l'erreur est remontée au client.

## Image utilisée

`docker:cli` (officielle, Alpine) : CLI Docker, plugin Compose et Buildx. Le
script installe `git` et `openssh-client` **uniquement s'ils sont absents**, la
composition exacte de l'image variant d'une version à l'autre.

## Ce que voit l'utilisateur

Le backend streame `docker logs -f esp32-updater` vers le navigateur. Quand
Compose recrée le backend, la connexion se coupe : le frontend affiche
« Connexion interrompue — le serveur redémarre probablement ». C'est le
déroulement normal ; l'updater, lui, termine son travail.

## Première mise à jour après ce correctif

Le backend actuellement déployé exécute encore l'**ancien** code. Il faut donc
faire cette mise à jour-ci **manuellement sur l'hôte**, une seule fois :

```bash
cd /root/Sereveur-ESP-32
git pull origin main
docker compose up -d --build
```

Les suivantes passeront par le bouton.

## Variables d'environnement

| Variable            | Défaut                  | Rôle                                     |
|---------------------|-------------------------|------------------------------------------|
| `HOST_PROJECT_PATH` | `/root/Sereveur-ESP-32` | Chemin du projet **sur l'hôte**          |
| `HOST_SSH_DIR`      | `/root/.ssh`            | Clé de déploiement de l'hôte             |
| `HOST_PROJECT_DIR`  | `/host-project`         | Montage du dépôt **dans le backend**     |
| `UPDATER_IMAGE`     | `docker:cli`            | Image du conteneur de mise à jour        |
