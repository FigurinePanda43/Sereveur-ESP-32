# Audit de sécurité — 2026-09-04

Audit du portail de publication de services locaux (backend FastAPI, proxy Caddy,
tunnel Cloudflare). Chaque faille exploitable a été **reproduite** avant correction,
avec le binaire Caddy réel et des services factices, puis la correction a été
**revérifiée** de la même façon.

Résumé : 1 critique, 3 élevées, 4 moyennes corrigées ; 3 points de moindre gravité
documentés ; 1 hypothèse d'attaque testée et écartée.

---

## CRITIQUE

### C1 — Contournement total de l'authentification des services protégés via WebSocket

**Où :** `backend/services/caddy.py`, génération du bloc « protected ».

**Avant :**
```
@notws not header Upgrade websocket
forward_auth @notws backend:8000 { uri /auth/check }
reverse_proxy <device>
```
Le matcher `@notws` excluait de l'authentification toute requête portant l'en-tête
`Upgrade: websocket`. **Résultat : n'importe qui, sans être connecté, atteignait un
service protégé en ajoutant simplement cet en-tête.** Beaucoup d'interfaces visées
(Proxmox, Home Assistant, consoles ESP32) utilisent des WebSockets ; l'en-tête est
trivial à ajouter même sur une requête HTTP ordinaire.

**Reproduction (Caddy réel + service factice) :**
```
curl -H "Host: prot.DOMAIN" -H "Upgrade: websocket" -H "Connection: Upgrade" \
     http://proxy/secret
→ HTTP 200, service atteint, AUCUN appel à /auth/check
```

**Correction :** `forward_auth` s'applique désormais à **toutes** les requêtes, y
compris les handshakes WebSocket. Vérifié : requête WebSocket non authentifiée →
**403** ; requête WebSocket avec cookie admin valide → service atteint (les
WebSockets légitimes fonctionnent toujours).

---

## ÉLEVÉ

### H1 — Durée de vie du jeton non plafonnée, aucune révocation

**Où :** `backend/auth.py`.

`SESSION_MAX_AGE` était lu depuis l'environnement sans borne haute : une mauvaise
configuration pouvait rendre un jeton valable des mois. Aucun moyen non plus de
révoquer les sessions en cours (jeton HMAC sans état).

**Correction :**
- Plafond **dur à 31 jours** : toute valeur supérieure de `SESSION_MAX_AGE_SECONDS`
  est ramenée à ce plafond (avec avertissement au démarrage). Répond à l'exigence
  « pas plus d'un mois ». Le défaut reste 30 jours.
- Levier de révocation globale `AUTH_MIN_ISSUED_AT` (epoch Unix) : les jetons émis
  avant cet instant sont refusés. Changer `APP_SECRET_KEY` invalide aussi tous les
  jetons existants.

### H2 — Protection anti-force brute trop permissive

**Où :** `backend/auth.py`, `apply_brute_force_rules`.

Ancien premier palier : **50 échecs en 10 minutes** avant tout blocage — largement
suffisant pour une attaque par dictionnaire, d'autant que le blocage est par IP.

**Correction :** premier palier ramené à **10 échecs en 15 minutes → blocage 15 min**,
réglable via `BRUTEFORCE_MAX_ATTEMPTS` / `BRUTEFORCE_WINDOW_MINUTES` /
`BRUTEFORCE_BLOCK_MINUTES`. Paliers supérieurs resserrés (20/h, 50/24h). Le mot de
passe admin est haché en bcrypt (coût par essai déjà élevé).

*Note :* le blocage reste par IP. Derrière Cloudflare, l'IP client provient de
`cf-connecting-ip` (positionné par Cloudflare, non usurpable via le tunnel) —
vérifié en lecture de code. Une attaque distribuée sur de nombreuses IP contourne
tout blocage par IP : c'est une limite inhérente, pas propre à ce projet.

### H3 — Le cookie de session est transmis aux équipements

**Où :** conception du SSO (cookie `.DOMAIN`) + `backend/services/caddy.py`.

Le cookie de session est déposé sur `.DOMAIN`. Le navigateur l'envoie donc à
**chaque** sous-domaine d'équipement, et Caddy le transmet tel quel à l'équipement
en amont. **Un équipement compromis (firmware modifié, panneau vérolé, ou même un
équipement public malveillant) peut capter le cookie d'administration** et prendre
le contrôle du portail — donc, via le terminal, de la machine hôte.

**Reproduction :** requête vers un service protégé avec `Cookie: esp32_session=…`
→ le service factice en amont reçoit le cookie d'administration intact.

**Correction :** l'en-tête `Cookie` est retiré des requêtes proxifiées vers les
équipements (`PROXY_STRIP_COOKIES`, **activé par défaut**). Le contrôle d'accès
n'est pas affecté : `forward_auth` lit le cookie via une sous-requête distincte,
effectuée avant ce retrait (vérifié). Le retrait s'applique à tous les modes
(protégé, public, public temporaire), car le cookie fuit dans tous les cas.

Ce défaut correspond au cas d'usage du projet : un mini serveur web d'équipement
sans authentification par cookie propre, le portail assurant toute la sécurité en
amont. À passer à `PROXY_STRIP_COOKIES=false` uniquement si un équipement protégé
possède sa propre connexion par cookie sur le domaine du portail (Proxmox, Home
Assistant), sans quoi ce cookie propre ne lui parviendrait plus.

Un retrait *chirurgical* (ne retirer que `esp32_session`, en conservant d'éventuels
cookies propres) a été tenté via le remplacement d'en-tête de Caddy mais s'est
révélé **non déterministe** aux tests (résultats variables selon l'ordre des
cookies) ; il n'a donc pas été retenu au profit du retrait total.

---

## MOYEN

### M1 — Absence d'en-têtes de sécurité (clickjacking du portail)

Le portail ne renvoyait aucun en-tête de sécurité : le tableau de bord (avec ses
actions terminal root et mise à jour) pouvait être chargé dans une iframe et
victime de clickjacking. **Corrigé** : `X-Frame-Options: DENY`,
`Content-Security-Policy: frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`,
`Referrer-Policy: no-referrer` sur toutes les réponses du portail (les équipements,
servis directement par Caddy, ne sont pas concernés).

### M2 — Redirection ouverte après connexion (`?next=`)

Le paramètre `next` était suivi sans validation : `…/auth/login?next=https://evil.com`
redirigeait la victime après une connexion réussie. **Corrigé** : `_safe_next()`
n'autorise qu'un chemin relatif ou une URL du domaine du portail.

### M3 — Scripts CDN sans contrôle d'intégrité (SRI)

`xterm` était chargé depuis un CDN sans `integrity` : une altération du CDN aurait
injecté du code dans le terminal admin. **Corrigé** : `integrity` (SHA-384) +
`crossorigin` sur les trois ressources.

### M4 — `local_ip` sans garde-fou (SSRF / métadonnées cloud)

`local_ip` acceptait n'importe quelle IP valide. Un équipement passé en accès
**public** et pointé vers `127.0.0.1`, `169.254.169.254` (métadonnées cloud) ou
l'API admin de Caddy aurait exposé un service interne sur Internet via le proxy.
**Corrigé** : rejet du loopback, du lien-local, de l'adresse non spécifiée et du
multicast, à la création comme à la modification.

---

## MOINDRE GRAVITÉ (documenté, non modifié)

- **Socket Docker + `/root/.ssh` montés dans le backend.** Toute exécution de code
  dans le backend donne un accès root à l'hôte. C'est inhérent aux fonctions
  « terminal serveur » et « mise à jour » (élévation par conception, protégée par
  la session admin). Déjà noté dans la dette technique.
- **API admin Caddy sur `0.0.0.0:2019`.** Non publiée vers l'hôte (seul `80:80`
  l'est), donc joignable uniquement sur le réseau Docker interne. Le backend en a
  besoin. Acceptable ; à garder à l'esprit si d'autres conteneurs rejoignent ce
  réseau.
- **`verify=False` pour la surveillance et le scan.** Acceptable pour des
  équipements LAN à certificat auto-signé ; ne concerne pas le trafic exposé.

## Hypothèse testée et ÉCARTÉE

- **Usurpation de slug via `X-Forwarded-Host` vers `/auth/check`.** On pouvait
  craindre qu'un client injecte cet en-tête pour se faire autoriser sur un autre
  service. Test avec Caddy réel : **Caddy écrase `X-Forwarded-Host`** par l'hôte
  réel du vhost ; l'en-tête fourni par le client n'atteint jamais `/auth/check`.
  Non exploitable.

---

## Réponses directes aux questions posées

| Question | Réponse |
|----------|---------|
| Nombre d'essais max pour un mot de passe | Était 50 / 10 min avant blocage → **ramené à 10 / 15 min**, réglable. |
| Contournement d'un service protégé une fois publié | **Oui, il en existait un** (WebSocket) — corrigé et revérifié. |
| Durée de vie du jeton ≤ 1 mois | Défaut 30 j, désormais **plafonné en dur à 31 j** ; révocation possible. |
| Autres failles non vues | Fuite du cookie aux équipements (retrait **activé par défaut**), clickjacking, redirection ouverte, CDN sans SRI, `local_ip` sans garde-fou — toutes traitées. |
