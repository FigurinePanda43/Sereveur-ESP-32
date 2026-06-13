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

## Alternative : Authentification locale (non implémentée)

Si Cloudflare Access n'est pas disponible :

- Ajout d'un middleware FastAPI avec sessions HTTP
- Table `users` en base avec hash bcrypt
- Login via formulaire `/login`
- Session cookie sécurisé (HttpOnly, SameSite=Strict)
- Variable d'environnement : `SECRET_KEY`

**Statut** : non implémentée, voir `docs/open_questions.md`

## Alternative : OAuth Google (non implémentée)

Via Cloudflare Access (IdP Google) ou `authlib` + FastAPI.

## Contrainte de sécurité

Cloudflare Access **doit** être activé avant toute mise en production. Sans cela, le portail est accessible publiquement.

## Plan d'implémentation

- [ ] Activer Cloudflare Access (configuration manuelle dans le dashboard)
- [ ] Vérifier que les headers `Cf-Access-Jwt-Assertion` sont présents (middleware de vérification)
- [ ] Optionnel : implémentation de l'authentification locale comme fallback
