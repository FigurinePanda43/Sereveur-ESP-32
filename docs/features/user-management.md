# Fonctionnalité : Gestion des utilisateurs

## Objectif

Permettre à l'administrateur de créer, depuis l'interface web, des **utilisateurs
secondaires** (identifiant + mot de passe) qui accèdent uniquement à un ou plusieurs
services choisis, éventuellement pour une durée limitée (date et heure de validité,
ou sans limite).

Cette fonctionnalité se greffe sur le mode d'accès **🔒 Protégé** d'un service :
en plus de l'administrateur, les utilisateurs autorisés peuvent alors accéder au
service, tant que leur autorisation est valide.

## Fonctionnement

### Modèle de données

| Table                 | Rôle                                                             |
|-----------------------|------------------------------------------------------------------|
| `users`               | Compte utilisateur (identifiant, hash bcrypt, validité, état)    |
| `user_device_access`  | Association utilisateur ↔ service autorisé (n-n)                  |

Colonnes de `users` :

| Champ           | Type      | Détail                                             |
|-----------------|-----------|----------------------------------------------------|
| `id`            | INTEGER   | PK                                                 |
| `username`      | TEXT      | UNIQUE, 3–32 caractères (`[a-zA-Z0-9._-]`)         |
| `password_hash` | TEXT      | Hash bcrypt                                        |
| `enabled`       | BOOLEAN   | Compte actif/désactivé                            |
| `valid_until`   | DATETIME  | Date d'expiration UTC ou `NULL` (sans limite)      |
| `description`   | TEXT      | Note optionnelle                                   |
| `created_at`    | DATETIME  | Création                                          |
| `last_login`    | DATETIME  | Dernière connexion réussie                        |

### Authentification et rôles

Le jeton de session (cookie signé HMAC) porte désormais un **rôle** :

- `admin` : session administrateur (identifiants issus de l'environnement). Accès
  complet au portail, aux API `/api/*` et à tous les services.
- `user` : utilisateur secondaire. **Aucun** accès au portail d'administration ni
  aux API. Le jeton n'est accepté que par le contrôle `forward_auth` de Caddy, et
  uniquement pour les services attribués et non expirés.

La page de connexion `/auth/login` est commune : elle tente d'abord l'administrateur,
puis les comptes utilisateurs.

### Contrôle d'accès (`/auth/check`, appelé par Caddy)

Pour un service en mode protégé, Caddy interroge `/auth/check` :

- Administrateur authentifié → `200` (accès à tout).
- Utilisateur authentifié → `200` seulement si le service (déduit de l'en-tête
  `X-Forwarded-Host`) lui est attribué, que son compte est actif et non expiré ;
  sinon page `403` « accès refusé ».
- Non authentifié → redirection `302` vers la page de connexion.

La validité est vérifiée **à chaque requête**, donc l'expiration est immédiate sans
resynchronisation de Caddy.

## API

| Méthode | Endpoint             | Description                    |
|---------|----------------------|--------------------------------|
| GET     | `/api/users/`        | Liste des utilisateurs         |
| POST    | `/api/users/`        | Crée un utilisateur            |
| GET     | `/api/users/{id}`    | Détail d'un utilisateur        |
| PUT     | `/api/users/{id}`    | Modifie un utilisateur         |
| DELETE  | `/api/users/{id}`    | Supprime un utilisateur        |

Toutes ces routes exigent une session **administrateur**.

## Interface

L'interface d'administration est organisée en **onglets** (navigation) :

- **Équipements** : tableau de bord des services (inchangé).
- **Utilisateurs** : liste, création et édition des comptes, choix des services
  autorisés (cases à cocher) et de la date de validité.
- **Outils** : scan réseau, terminal serveur et mise à jour (regroupés pour
  désencombrer l'en-tête).

## Points d'attention

- Un utilisateur n'a d'effet que sur les services en mode **Protégé**. Un service
  **Public** reste accessible sans authentification ; un service **Suspendu** est
  inaccessible à tous.
- L'identifiant de l'administrateur (`ADMIN_USER`) ne peut pas être réutilisé pour
  un compte utilisateur.
- Les mots de passe sont stockés hachés (bcrypt) ; ils ne sont jamais renvoyés par
  l'API. Laisser le champ vide en édition conserve le mot de passe existant.
