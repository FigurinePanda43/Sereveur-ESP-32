#!/usr/bin/env bash
# Backup the SQLite database to the backups/ directory with a timestamp.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"

BACKUP_DIR="backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/devices_${TIMESTAMP}.db"

mkdir -p "$BACKUP_DIR"

# Vérifier que le conteneur backend tourne
if ! docker compose ps backend | grep -q "Up"; then
    echo "ERREUR : le conteneur 'backend' n'est pas démarré."
    echo "  Lancer scripts/start.sh d'abord, ou copier le fichier volumes manuellement."
    exit 1
fi

# Copie depuis le volume Docker
echo "Sauvegarde en cours..."
docker compose exec -T backend cp /data/devices.db /tmp/esp32_backup.db
docker compose cp backend:/tmp/esp32_backup.db "$BACKUP_FILE"
docker compose exec -T backend rm -f /tmp/esp32_backup.db

echo "=== Sauvegarde créée : ${BACKUP_FILE} ==="
echo ""
echo "Sauvegardes disponibles :"
ls -lh "$BACKUP_DIR"/*.db 2>/dev/null || echo "Aucune."
