# Fonctionnalité : Enregistrement d'un équipement

## Objectif

Permettre l'ajout d'un ESP32 en moins de 30 secondes via un formulaire web, avec création automatique de tous les accès.

## Fonctionnement

1. L'utilisateur soumet le formulaire (nom, slug, IP, port, description)
2. Le backend valide les champs (format IP, unicité du slug, slugs réservés)
3. L'enregistrement est créé en base de données
4. Le DNS Cloudflare est créé via API
5. La configuration Caddy est resynchronisée
6. L'équipement apparaît immédiatement dans le tableau de bord

## Architecture

```
POST /api/devices/
  → Validation Pydantic
  → INSERT devices (SQLite)
  → cloudflare.create_dns_record(slug)
  → caddy.sync_caddy(all_devices)
  → Retour 201 + DeviceResponse
```

## API impactées

- `POST /api/devices/` — création
- `PUT /api/devices/{id}` — modification (resync Caddy)
- `DELETE /api/devices/{id}` — suppression (suppression DNS + resync Caddy)

## Base de données impactée

Table `devices` — INSERT / UPDATE / DELETE.

## Validation des champs

| Champ       | Règle                                       |
|-------------|---------------------------------------------|
| `slug`      | `^[a-z0-9-]+$`, non réservé (iot, api, www…) |
| `local_ip`  | IP valide (ipaddress.ip_address)            |
| `local_port`| Entier entre 1 et 65535                     |

## Slugs réservés

`iot`, `api`, `www`, `mail`, `ftp`, `admin`, `root`, `docs`

## Risques

- Slug en doublon → HTTP 409 (géré)
- DNS Cloudflare non configuré → warning loggé, équipement créé quand même
- Caddy indisponible au démarrage → retry automatique dans lifespan

## Plan d'implémentation

- [x] Schéma Pydantic `DeviceCreate`
- [x] Route `POST /api/devices/`
- [x] Appel `cloudflare.create_dns_record`
- [x] Appel `caddy.sync_caddy`
- [x] Validation côté frontend (JS)
- [x] Modal de formulaire dans le dashboard
