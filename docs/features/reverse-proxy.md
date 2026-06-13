# Fonctionnalité : Reverse Proxy dynamique (Caddy)

## Objectif

Router automatiquement le trafic entrant vers le bon équipement ou vers le portail principal, sans rechargement de service et sans fichier de configuration statique.

## Fonctionnement

Caddy démarre avec une configuration minimale (admin API activée sur `:2019`).

À chaque changement (ajout, modification, suppression d'équipement), le backend envoie la configuration complète à l'endpoint `POST /load` de l'admin API Caddy. Caddy applique la nouvelle configuration de façon atomique, sans interruption des connexions existantes.

### Routes configurées

| Host                   | Destination        |
|------------------------|--------------------|
| `iot.DOMAIN`           | `backend:8000`     |
| `slug1.DOMAIN`         | `192.168.x.x:port` |
| `slug2.DOMAIN`         | `192.168.x.y:port` |

## Architecture

```
services/caddy.py
  sync_caddy(devices)
    → Construit la config JSON complète
    → POST http://caddy:2019/load
    → Caddy applique atomiquement
```

### Exemple de config JSON envoyée à Caddy

```json
{
  "admin": {"listen": "0.0.0.0:2019"},
  "apps": {
    "http": {
      "servers": {
        "main": {
          "listen": [":80"],
          "routes": [
            {
              "match": [{"host": ["iot.mondomaine.com"]}],
              "handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "backend:8000"}]}]
            },
            {
              "match": [{"host": ["cuve-go.mondomaine.com"]}],
              "handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "192.168.1.45:80"}]}]
            }
          ]
        }
      }
    }
  }
}
```

## Déclencheurs de synchronisation

- Démarrage du backend (`lifespan`)
- `POST /api/devices/` (création)
- `PUT /api/devices/{id}` (modification)
- `DELETE /api/devices/{id}` (suppression)

## Risques

- Caddy non démarré lors du premier appel → retry avec backoff dans lifespan
- Caddy redémarré → la config est perdue ; le backend la réapplique au prochain appel (ou au prochain démarrage)
- Configuration avec 0 équipements : seule la route principale est présente (comportement attendu)

## Plan d'implémentation

- [x] `sync_caddy(devices)` avec `POST /load`
- [x] Route principale `iot.DOMAIN` toujours incluse
- [x] Retry au démarrage si Caddy indisponible
- [ ] Endpoint `/api/admin/sync-caddy` pour forcer une resynchronisation manuelle (future)
