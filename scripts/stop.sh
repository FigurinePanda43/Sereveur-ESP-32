#!/usr/bin/env bash
# Stop all ESP32 Manager services (containers remain, volumes preserved).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"

echo "=== Arrêt ESP32 Manager ==="
docker compose down

echo "Données préservées dans les volumes Docker."
echo "Pour supprimer les volumes : docker compose down -v"
