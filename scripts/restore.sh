#!/usr/bin/env bash
# Restore the SQLite database from a backup file.
# Usage: bash scripts/restore.sh [path/to/backup.db]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"

BACKUP_DIR="backups"

# Si aucun argument : afficher les sauvegardes disponibles
if [ $# -eq 0 ]; then
    echo "Usage : bash scripts/restore.sh <fichier_backup>"
    echo ""
    echo "Sauvegardes disponibles :"
    if ls "$BACKUP_DIR"/*.db &>/dev/null; then
        ls -lh "$BACKUP_DIR"/*.db
    else
        echo "  Aucune sauvegarde dans ${BACKUP_DIR}/"
    fi
    exit 0
fi

BACKUP_FILE="$1"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "ERREUR : fichier '${BACKUP_FILE}' introuvable."
    exit 1
fi

echo "Fichier de restauration : ${BACKUP_FILE}"
echo "ATTENTION : les données actuelles seront remplacées."
read -r -p "Confirmer la restauration ? [y/N] " confirm

if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "Annulé."
    exit 0
fi

# Sauvegarde automatique de sécurité avant restauration
SAFETY_BACKUP="${BACKUP_DIR}/pre_restore_$(date +%Y%m%d_%H%M%S).db"
if docker compose ps backend | grep -q "Up"; then
    echo "Sauvegarde de sécurité → ${SAFETY_BACKUP}"
    docker compose exec -T backend cp /data/devices.db /tmp/safety_backup.db 2>/dev/null || true
    docker compose cp backend:/tmp/safety_backup.db "$SAFETY_BACKUP" 2>/dev/null || true
fi

echo "Arrêt du backend..."
docker compose stop backend

echo "Restauration en cours..."
docker compose cp "$BACKUP_FILE" backend:/data/devices.db

echo "Redémarrage du backend..."
docker compose start backend

echo ""
echo "=== Restauration terminée depuis : ${BACKUP_FILE} ==="
