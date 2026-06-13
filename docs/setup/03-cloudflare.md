# Étape 3 — Configuration Cloudflare

## Prérequis

- Domaine enregistré chez un bureau d'enregistrement quelconque
- Compte Cloudflare (gratuit suffisant)
- Le domaine doit être géré par Cloudflare (nameservers Cloudflare)

---

## 3.1 — Ajouter le domaine à Cloudflare

Si le domaine n'est pas encore sur Cloudflare :

1. Se connecter à https://dash.cloudflare.com
2. Cliquer **Add a Site**
3. Saisir le domaine (ex : `mondomaine.com`)
4. Choisir le plan **Free**
5. Cloudflare affiche les nameservers à configurer
6. Aller sur le registrar du domaine et remplacer les nameservers par ceux de Cloudflare
7. Attendre la propagation (5 min à 24h)

---

## 3.2 — Récupérer le Zone ID

1. Aller sur https://dash.cloudflare.com
2. Cliquer sur le domaine
3. Dans l'onglet **Overview**, colonne de droite
4. Copier le **Zone ID**

→ À mettre dans `.env` : `CLOUDFLARE_ZONE_ID=<zone-id>`

---

## 3.3 — Créer un token API Cloudflare

1. Aller sur https://dash.cloudflare.com/profile/api-tokens
2. Cliquer **Create Token**
3. Choisir **Custom token**
4. Configurer les permissions :

| Ressource | Permission  |
|-----------|-------------|
| Zone > DNS | Edit       |
| Zone > Zone | Read      |

5. Dans **Zone Resources** : sélectionner **Specific zone** → votre domaine
6. Cliquer **Continue to summary** → **Create Token**
7. **Copier le token immédiatement** (affiché une seule fois)

→ À mettre dans `.env` : `CLOUDFLARE_API_TOKEN=<token>`

---

## 3.4 — Créer le tunnel Cloudflare

1. Aller sur https://one.dash.cloudflare.com
2. **Networks** → **Tunnels** → **Create a tunnel**
3. Choisir **Cloudflared**
4. Nommer le tunnel : `esp32-manager`
5. Cliquer **Save tunnel**
6. Choisir **Docker** comme environnement
7. Copier le token affiché (commence par `eyJ...`)

→ À mettre dans `.env` : `CLOUDFLARE_TUNNEL_TOKEN=<token>`

### Récupérer le Tunnel ID

Après création, dans la liste des tunnels :
- Cliquer sur `esp32-manager`
- L'URL contient le Tunnel ID : `.../tunnels/<TUNNEL-ID>/...`

Ou via la liste : copier l'UUID visible dans le tableau.

→ À mettre dans `.env` : `CLOUDFLARE_TUNNEL_ID=<tunnel-id>`

---

## 3.5 — Configurer les ingress rules du tunnel

Dans le dashboard du tunnel `esp32-manager` :

1. Onglet **Public Hostname**
2. **Add a public hostname**

| Champ       | Valeur                      |
|-------------|----------------------------|
| Subdomain   | `*` (wildcard)              |
| Domain      | `mondomaine.com`            |
| Service     | `HTTP`                      |
| URL         | `caddy:80`                  |

3. Sauvegarder

> Cette règle unique couvre à la fois `iot.mondomaine.com` et tous les sous-domaines d'équipements.

---

## 3.6 — Créer le DNS pour le portail principal

1. Dans Cloudflare Dashboard → votre domaine → **DNS** → **Records**
2. Ajouter un enregistrement :

| Type  | Nom | Valeur                            | Proxy |
|-------|-----|-----------------------------------|-------|
| CNAME | iot | `<TUNNEL-ID>.cfargotunnel.com`    | Oui   |

> Cet enregistrement est créé **une seule fois**. Les DNS des équipements sont ensuite créés automatiquement par l'application.

---

## 3.7 — Vérifier la configuration

```bash
# Depuis la VM
curl -I https://iot.mondomaine.com
# Doit retourner un code HTTP (200, 502, etc.) sans erreur SSL
```

---

## Variables .env résultantes

```env
DOMAIN=mondomaine.com
CLOUDFLARE_API_TOKEN=<token-créé-en-3.3>
CLOUDFLARE_ZONE_ID=<zone-id-récupéré-en-3.2>
CLOUDFLARE_TUNNEL_ID=<tunnel-id-récupéré-en-3.4>
CLOUDFLARE_TUNNEL_TOKEN=<token-tunnel-récupéré-en-3.4>
```

**Étape suivante → [04-first-launch.md](./04-first-launch.md)**
