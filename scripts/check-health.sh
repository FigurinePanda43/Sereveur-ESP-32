#!/usr/bin/env bash
# Check health of all Gestionnaire de publication de services locaux components.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"

ALL_OK=true

echo "=== Vérification de santé Gestionnaire de publication de services locaux ==="
echo ""

# ── Conteneurs Docker ──────────────────────────────────────────────────────────
echo "--- Conteneurs Docker ---"
docker compose ps
echo ""

check_container() {
    local name="$1"
    if docker compose ps "$name" 2>/dev/null | grep -q "Up"; then
        echo "  [OK] $name"
    else
        echo "  [KO] $name — non démarré"
        ALL_OK=false
    fi
}

check_container "backend"
check_container "caddy"
check_container "cloudflared"
echo ""

# ── API Backend ────────────────────────────────────────────────────────────────
echo "--- API Backend (port 8000 interne) ---"
HEALTH=$(docker compose exec -T backend curl -sf http://localhost:8000/api/health 2>/dev/null || echo "")
if [ -n "$HEALTH" ]; then
    echo "  [OK] ${HEALTH}"
else
    echo "  [KO] API backend inaccessible"
    ALL_OK=false
fi
echo ""

# ── Caddy Admin API ────────────────────────────────────────────────────────────
echo "--- Caddy Admin API (port 2019 interne) ---"
CADDY_OK=$(docker compose exec -T caddy wget -qO- http://localhost:2019/config/ 2>/dev/null | head -c 50 || echo "")
if [ -n "$CADDY_OK" ]; then
    echo "  [OK] Caddy répond"
else
    echo "  [KO] Caddy admin API inaccessible"
    ALL_OK=false
fi
echo ""

# ── Tunnel Cloudflare ──────────────────────────────────────────────────────────
echo "--- Tunnel Cloudflare ---"
if docker compose logs cloudflared --tail=10 2>/dev/null | grep -qi "connection registered\|tunnel connected\|Registered tunnel"; then
    echo "  [OK] Tunnel connecté"
elif docker compose logs cloudflared --tail=10 2>/dev/null | grep -qi "error\|failed"; then
    echo "  [KO] Tunnel en erreur — voir : docker compose logs cloudflared"
    ALL_OK=false
else
    echo "  [??] Statut du tunnel inconnu — voir : docker compose logs cloudflared"
fi
echo ""

# ── Résumé ─────────────────────────────────────────────────────────────────────
echo "--- Résumé ---"
if $ALL_OK; then
    echo "  Tous les composants sont opérationnels."
else
    echo "  Des composants présentent des problèmes. Voir les détails ci-dessus."
    exit 1
fi
