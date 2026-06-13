# Fonctionnalité : Gestion Cloudflare

## Objectif

Créer et supprimer automatiquement les enregistrements DNS Cloudflare pour chaque équipement, sans aucune intervention manuelle.

## Fonctionnement

### Création (ajout d'équipement)

Création d'un enregistrement CNAME :
- **Nom** : `slug.DOMAIN`
- **Valeur** : `<TUNNEL_ID>.cfargotunnel.com`
- **Proxied** : oui (trafic via Cloudflare)
- **TTL** : auto (1)

### Suppression (retrait d'équipement)

1. Recherche de l'enregistrement DNS par nom
2. Récupération de l'ID de l'enregistrement
3. Suppression via API

## Architecture

```
services/cloudflare.py
  create_dns_record(slug) → POST /zones/{zone_id}/dns_records
  delete_dns_record(slug) → GET /zones/{zone_id}/dns_records?name=slug.DOMAIN
                          → DELETE /zones/{zone_id}/dns_records/{record_id}
```

## Variables d'environnement requises

| Variable               | Utilisation                     |
|------------------------|---------------------------------|
| `CLOUDFLARE_API_TOKEN` | Authentification Bearer         |
| `CLOUDFLARE_ZONE_ID`   | Identification du domaine       |
| `CLOUDFLARE_TUNNEL_ID` | Valeur cible du CNAME           |
| `DOMAIN`               | Construction du nom DNS         |

## Comportement si Cloudflare non configuré

Si une variable est absente ou vide, la fonction logue un warning et retourne `False`. L'équipement est quand même créé en base. L'opération DNS peut être relancée ultérieurement.

## API Cloudflare utilisée

`https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records`

## Risques

- Rate limiting Cloudflare (1200 req/5min) : non géré actuellement
- Token expiré → erreur 401 loggée
- CNAME déjà existant → erreur 409 Cloudflare (à logger, pas critique)

## Plan d'implémentation

- [x] `create_dns_record(slug)`
- [x] `delete_dns_record(slug)`
- [ ] Gestion du rate limiting (future)
- [ ] Création automatique du DNS pour `iot.DOMAIN` au premier démarrage (future)
