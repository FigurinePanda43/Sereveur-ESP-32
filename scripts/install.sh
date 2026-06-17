#!/usr/bin/env bash
# Install Gestionnaire de publication de services locaux: prepare .env, create directories, pull Docker images.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "=== Installation Gestionnaire de publication de services locaux ==="

# Vérification des dépendances
if ! command -v docker &>/dev/null; then
    echo "ERREUR : Docker non trouvé. Consulter docs/setup/02-docker.md"
    exit 1
fi

if ! docker compose version &>/dev/null; then
    echo "ERREUR : Docker Compose (plugin) non trouvé."
    exit 1
fi

# Copie du .env
if [ ! -f .env ]; then
    cp .env.example .env

    # Génération de la clé secrète
    if command -v openssl &>/dev/null; then
        SECRET=$(openssl rand -hex 32)
    else
        SECRET=$(head -c 32 /dev/urandom | base64 | tr -d '+/=' | head -c 64)
    fi

    sed -i "s|APP_SECRET_KEY=changeme|APP_SECRET_KEY=${SECRET}|" .env

    echo "→ .env créé avec une clé secrète générée automatiquement."
    echo "  Compléter les valeurs Cloudflare avant de lancer l'application."
    echo "  Commande : nano .env"
else
    echo "→ .env existant conservé (non écrasé)."
fi

# Création des répertoires
mkdir -p data backups

# Pull des images Docker
echo ""
echo "→ Téléchargement des images Docker..."
docker compose pull

echo ""
echo "=== Installation terminée ==="
echo ""
echo "Étapes suivantes :"
echo "  1. Compléter .env avec vos tokens Cloudflare"
echo "  2. bash scripts/start.sh"
echo "  3. Ouvrir https://iot.VOTRE-DOMAINE"
