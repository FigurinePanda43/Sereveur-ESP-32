# Fonctionnalité : Authentification

## Objectif

Protéger l'accès au portail `iot.DOMAIN` et aux interfaces ESP32 contre tout accès non autorisé depuis internet.

## Solution retenue : Cloudflare Access (prioritaire)

Cloudflare Access est un service Zero Trust qui intercepte les requêtes AVANT qu'elles n'atteignent le tunnel, sans modification de l'application.

### Configuration (manuelle, dans Cloudflare Zero Trust)

1. Créer une application de type "Self-hosted"
2. URL : `iot.DOMAIN` et `*.DOMAIN`
3. Politique : autoriser les emails du domaine souhaité, ou une liste d'emails spécifiques
4. Méthode d'authentification : OTP email, Google OAuth, ou autre IdP

### Avantages

- Aucun code côté application
- Protection au niveau du réseau Cloudflare
- Support SSO / MFA natif
- Audit logs automatiques

## Authentification locale (implémentée)

Utilisable seule ou en complément de Cloudflare Access :

- Middleware FastAPI de session (cookie signé HMAC, `HttpOnly`, `Secure`, `SameSite=Lax`)
- Compte administrateur via l'environnement : `ADMIN_USER` + `ADMIN_PASSWORD_HASH` (bcrypt)
- Login via formulaire `/auth/login`, déconnexion `/auth/logout`
- Protection anti-bruteforce (journalisation des tentatives, blocage d'IP progressif)
- Variable d'environnement : `APP_SECRET_KEY` (signature des sessions)
- Le jeton porte un **rôle** (`admin` / `user`) ; seul `admin` accède au portail et aux API

**Comptes utilisateurs à accès limité** : l'administrateur peut créer des comptes
supplémentaires autorisés uniquement pour certains services et jusqu'à une date de
validité. Voir `docs/features/user-management.md`.

**Statut** : implémentée.

## Alternative : OAuth Google (non implémentée)

Via Cloudflare Access (IdP Google) ou `authlib` + FastAPI.

## Contrainte de sécurité

Cloudflare Access **doit** être activé avant toute mise en production. Sans cela, le portail est accessible publiquement.

## Plan d'implémentation

- [ ] Activer Cloudflare Access (configuration manuelle dans le dashboard)
- [ ] Vérifier que les headers `Cf-Access-Jwt-Assertion` sont présents (middleware de vérification)
- [ ] Optionnel : implémentation de l'authentification locale comme fallback
