#!/usr/bin/env bash
# Start all ESP32 Manager services via Docker Compose.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"

if [ ! -f .env ]; then
    echo "ERREUR : .env introuvable. Lancer scripts/install.sh d'abord."
    exit 1
fi

# Vérifier que les variables critiques sont renseignées
if grep -qE "^(CLOUDFLARE_TUNNEL_TOKEN=)$" .env 2>/dev/null; then
    echo "AVERTISSEMENT : CLOUDFLARE_TUNNEL_TOKEN vide dans .env"
    echo "  Le tunnel Cloudflare ne fonctionnera pas."
fi

echo "=== Démarrage ESP32 Manager ==="
docker compose up -d --remove-orphans

echo ""
echo "=== Statut des conteneurs ==="
docker compose ps

echo ""
echo "Logs disponibles : docker compose logs -f"
