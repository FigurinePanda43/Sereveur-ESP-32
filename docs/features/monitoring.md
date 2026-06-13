# Fonctionnalité : Surveillance des équipements

## Objectif

Vérifier périodiquement la disponibilité de chaque ESP32 et mettre à jour son statut dans la base de données, affiché en temps quasi-réel dans le tableau de bord.

## Fonctionnement

Une tâche asyncio tourne en arrière-plan, déclenchée au démarrage de l'application.

### Cycle de surveillance

1. Récupération de tous les équipements en base
2. Envoi parallèle d'une requête HTTP GET sur `http://local_ip:port`
3. Évaluation du résultat :
   - Code HTTP < 500 ET temps < 3s → `online`
   - Code HTTP < 500 ET temps ≥ 3s → `slow`
   - Exception / timeout / code ≥ 500 → `offline`
4. Mise à jour de `status` en base
5. Mise à jour de `last_seen` si online ou slow
6. Pause de 60 secondes

### Affichage dans le dashboard

| Statut    | Couleur   | Description             |
|-----------|-----------|-------------------------|
| `online`  | Vert      | Réponse normale         |
| `slow`    | Orange    | Réponse lente (> 3s)    |
| `offline` | Rouge     | Hors ligne / timeout    |
| `unknown` | Gris      | Jamais vérifié          |

## Architecture

```
services/monitor.py
  monitor_loop() — tâche asyncio infinie
    → SessionLocal() — nouvelle session DB par cycle
    → check_device(db, device) pour chaque équipement (gather)
      → httpx.AsyncClient GET http://ip:port (timeout 5s)
      → UPDATE devices SET status=?, last_seen=?
```

## Déclenchement manuel

`POST /api/devices/{id}/refresh` force une vérification immédiate et retourne le nouveau statut.

## Paramètres configurables (constantes dans monitor.py)

| Constante         | Valeur par défaut | Description              |
|-------------------|-------------------|--------------------------|
| `MONITOR_INTERVAL`| 60 secondes       | Intervalle entre cycles  |
| `SLOW_THRESHOLD`  | 3.0 secondes      | Seuil de réponse lente   |
| `HTTP_TIMEOUT`    | 5.0 secondes      | Timeout de la requête    |

## Risques

- Un ESP32 sans route GET sur `/` renverra une erreur → marqué offline
- Vérifications parallèles : si 50 équipements, 50 connexions simultanées → acceptable
- Si la tâche asyncio est annulée brutalement, la session DB est fermée proprement

## Plan d'implémentation

- [x] `monitor_loop()` en tâche asyncio
- [x] `check_device(db, device)` avec httpx async
- [x] Démarrage dans lifespan FastAPI
- [x] Annulation propre à l'arrêt de l'application
- [x] Endpoint `/api/devices/{id}/refresh`
- [ ] Configurer les seuils via variables d'environnement (future)
- [ ] Notification sur changement de statut (future)
- [ ] Historisation des statuts dans une table dédiée (future)
