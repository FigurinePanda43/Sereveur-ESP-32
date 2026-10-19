# Fonctionnalité : IPBan (sécurité)

## Objectif

Protéger le portail et les équipements publiés contre les attaques par force
brute, et contrôler **qui** peut atteindre le serveur : par adresse IP, par plage
et par pays. Tout se règle depuis l'onglet **Sécurité** du portail, sans éditer de
fichier.

## Ce que fait IPBan

| Fonction | Détail |
|---|---|
| Activation globale | Interrupteur « Protection IPBan ». Désactivé : rien n'est bloqué, les tentatives restent journalisées. |
| Bannissement automatique | Paliers configurables « N échecs de connexion en X min → bannie Y min ». Le plus long palier atteint s'applique. |
| Récidive | Chaque nouveau bannissement d'une même IP est multiplié (×2 par défaut), plafonné ; bannissement définitif optionnel au N-ième. |
| Bannissement manuel | Une IP, pour 1 h, 24 h, 7 j, 30 j ou définitivement, avec un motif. Déblocage en un geste. |
| Liste blanche | IP ou plages CIDR toujours autorisées : jamais bannies, jamais filtrées par pays. |
| Liste noire | IP ou plages CIDR toujours bloquées. |
| Mode de fonctionnement | **Standard** (tout le monde sauf les bannis/listes noires/pays filtrés) ou **Liste blanche uniquement**. |
| Filtrage par pays | Désactivé / **Autoriser seulement** ces pays / **Bloquer** ces pays. Tor (`T1`) et « inconnu » (`XX`) se filtrent comme des pays. |
| Périmètre | Portail seul, ou portail **et** tous les équipements (publics compris). |
| Réseau local | Exemption des IP privées (activée par défaut) : on ne s'enferme pas dehors depuis le LAN. |
| Compte-rendu | Sur 24 h / 7 j / 30 j : échecs, requêtes bloquées, bannissements, chronologie, adresses et pays les plus actifs, identifiants essayés, motifs de blocage. |
| Journal | Requêtes bloquées (agrégées), bannissements, déblocages, échecs de connexion, modifications de réglages. Filtrable, paginé. |
| Test d'une IP | Dit si une adresse (et un pays) serait autorisée, et pourquoi. |

## Ordre d'évaluation

Pour chaque requête, la première règle qui s'applique l'emporte :

1. IPBan désactivé, ou équipement hors périmètre → autorisé
2. IP en **liste blanche** → autorisée
3. IP **privée** et exemption activée → autorisée
4. IP **bannie** → bloquée (page « Accès temporairement bloqué », avec l'heure de fin)
5. IP en **liste noire** → bloquée
6. Mode **liste blanche uniquement** → bloquée
7. **Filtrage par pays**
8. Sinon → autorisée

## Où le filtrage s'applique

Le trafic passe toujours par Cloudflare Tunnel puis Caddy ; la décision est prise
par le backend.

```
Internet → Cloudflare (CF-Connecting-IP, CF-IPCountry) → Tunnel → Caddy
   iot.DOMAIN               → backend : middleware (IPBan avant tout le reste)
   slug.DOMAIN (protégé)    → forward_auth /auth/check  → middleware IPBan + session
   slug.DOMAIN (public)     → forward_auth /ipban/check → middleware IPBan
                              (ajouté par Caddy seulement si IPBan protège les équipements)
   WebSocket terminal       → contrôle explicite dans le handler (le middleware HTTP ne le voit pas)
```

- L'IP du client vient de `CF-Connecting-IP`, le pays de `CF-IPCountry`
  (géolocalisation Cloudflare : aucune base GeoIP à maintenir). Vérifier que
  *IP Geolocation* est activé dans Cloudflare → *Network* (c'est le cas par défaut).
- Une requête refusée reçoit un **403** : page autonome (aussi sur le sous-domaine
  d'un équipement) ou JSON pour `/api/*`.
- Les décisions s'appuient sur un instantané en mémoire (réglages, listes,
  bannissements actifs), rechargé à chaque modification et au plus tard toutes
  les 15 s : un équipement public ne coûte pas une requête SQL par hit.
- Les requêtes bloquées sont comptées en mémoire et écrites toutes les 30 s,
  fusionnées par (IP, motif, hôte) et par heure : une rafale ne remplit pas la base.
- Quand IPBan est désactivé ou ne protège que le portail, les équipements publics
  n'ont **aucun** `forward_auth` supplémentaire (zéro surcoût).

## Protection anti-verrouillage

Toute modification est d'abord évaluée sur la connexion de l'administrateur qui
la fait ; si elle le bloquerait (passage en liste blanche sans y figurer, pays non
autorisé, plage de la liste noire qui le couvre, retrait de sa propre entrée de
liste blanche…), elle est **refusée (409)** avec l'explication. On ne peut pas non
plus se bannir soi-même.

**En dernier recours** (accès perdu malgré tout) : ajouter `IPBAN_DISABLED=true`
dans `.env` puis `docker compose up -d backend`. IPBan est alors coupé quelle que
soit la configuration ; l'onglet Sécurité l'indique. Corriger les réglages, puis
retirer la variable.

## Réglages par défaut

| Réglage | Défaut |
|---|---|
| IPBan | activé |
| Mode | standard |
| Périmètre | portail et équipements |
| Réseau local exempté | oui |
| Filtrage par pays | désactivé |
| Paliers | 10 échecs / 15 min → 15 min ; 20 / 1 h → 1 h ; 50 / 24 h → 24 h |
| Récidive | ×2, plafond 30 jours, jamais définitif |
| Conservation du journal | 30 jours |

Le premier palier reprend `BRUTEFORCE_MAX_ATTEMPTS`, `BRUTEFORCE_WINDOW_MINUTES`
et `BRUTEFORCE_BLOCK_MINUTES` à la création des réglages : un déploiement existant
garde son comportement. Ensuite, tout se règle dans l'interface.

Après un bannissement (ou un déblocage), seuls les échecs **postérieurs** comptent :
les échecs déjà sanctionnés ne rebannissent pas à la première nouvelle tentative.

## API

Toutes les routes exigent une session administrateur.

| Méthode | Endpoint | Description |
|---|---|---|
| GET | `/api/security/settings` | Réglages + votre IP/pays et la décision qui vous concerne |
| PUT | `/api/security/settings` | Modification partielle (anti-verrouillage) |
| GET | `/api/security/bans?include_expired=` | IP bannies (ou historique) |
| POST | `/api/security/bans` | Bannir `{ip, duration_minutes (null = définitif), reason}` |
| DELETE | `/api/security/bans/{ip}` | Débloquer |
| GET | `/api/security/lists` | Listes blanche et noire |
| POST | `/api/security/lists` | Ajouter `{list_type: allow|deny, value, note}` |
| DELETE | `/api/security/lists/{id}` | Retirer une entrée |
| POST | `/api/security/test` | Tester `{ip, country, scope: portal|service}` |
| GET | `/api/security/report?period=24h|7d|30d` | Compte-rendu agrégé |
| GET | `/api/security/events?kind=&limit=&before=` | Journal paginé |

## Données

| Table | Rôle |
|---|---|
| `security_settings` | Une ligne : tous les réglages (paliers en JSON) |
| `ip_rules` | Entrées des listes blanche (`allow`) et noire (`deny`) |
| `blocked_ips` | Bannissements ; conservés après expiration pour la récidive (`ban_count`) |
| `security_events` | Journal (requêtes bloquées agrégées via `count`) |
| `auth_attempts` | Tentatives de connexion (colonne `country` ajoutée) |

Une tâche de fond purge chaque heure ce qui dépasse la durée de conservation
(les tentatives sont gardées au moins aussi longtemps que la plus longue fenêtre
de palier).

## Limites connues

- **Confiance dans les en-têtes Cloudflare.** Le port 80 de Caddy est publié sur
  l'hôte : une machine du réseau local qui l'atteint directement peut forger
  `CF-Connecting-IP` / `CF-IPCountry`. Depuis Internet, Cloudflare écrase ces
  en-têtes. Pour fermer cette porte, ne publier le port de Caddy que sur
  `127.0.0.1` ou le retirer (cloudflared le joint par le réseau Docker).
- Une attaque **distribuée** (une IP par essai) échappe au bannissement par IP ;
  le filtrage par pays et le mode liste blanche la limitent.
- Si la base est illisible, le filtrage est suspendu (et l'erreur journalisée)
  plutôt que de bloquer tout le monde, administrateur compris.
