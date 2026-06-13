# Questions ouvertes

Décisions nécessitant une validation avant implémentation.

---

## [OQ-001] Création automatique du DNS pour iot.DOMAIN

**Question :** Le DNS `iot.DOMAIN` doit-il être créé automatiquement au premier démarrage, ou doit-il rester une étape manuelle dans la procédure d'installation ?

**Contexte :** Actuellement, c'est une étape manuelle documentée dans le README. L'automatiser nécessiterait de détecter si l'enregistrement existe déjà pour éviter les doublons.

**Options :**
1. Rester manuel (actuel) — plus simple, un seul enregistrement à créer une fois
2. Automatiser dans le lifespan du backend

**Statut :** En attente de décision.

---

## [OQ-002] Authentification locale comme fallback

**Question :** Si Cloudflare Access n'est pas activé, doit-on implémenter une authentification locale (login/mot de passe) ?

**Contexte :** Cloudflare Access est la solution prioritaire. Une authentification locale protègerait quand même l'interface si Cloudflare Access n'est pas configuré.

**Options :**
1. Pas d'auth locale — l'opérateur doit configurer Cloudflare Access
2. Auth locale basique (email/mot de passe avec bcrypt + session cookie)

**Statut :** En attente de décision.

---

## [OQ-003] Ingress rules du tunnel Cloudflare

**Question :** L'ingress du tunnel (wildcard `*.DOMAIN` → `http://caddy:80`) doit-il être configuré via l'API Cloudflare Tunnel ou resté manuel via le dashboard ?

**Contexte :** L'API Cloudflare Tunnel permet de gérer les ingress programmatiquement. Cela permettrait de tout automatiser. Mais c'est une configuration faite une seule fois.

**Options :**
1. Manuel via dashboard (actuel)
2. Automatisé via API au premier démarrage

**Statut :** En attente de décision.

---

## [OQ-004] Comportement si l'ESP32 n'a pas de route GET sur /

**Question :** Certains ESP32 pourraient retourner 404 ou ne pas répondre sur `/`. Faut-il permettre de configurer un chemin de vérification personnalisé par équipement ?

**Contexte :** Actuellement, le monitor fait GET sur `http://ip:port`. Si l'équipement répond 404, il est marqué "online" (code < 500). Mais si l'équipement n'a pas de serveur HTTP du tout sur le port configuré, il sera "offline".

**Options :**
1. Conserver GET sur `/` (actuel) — simple
2. Ajouter un champ `health_path` (défaut `/`) par équipement

**Statut :** En attente de décision.
