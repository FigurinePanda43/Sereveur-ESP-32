# Étape 1 — Création de la VM sur Proxmox

## Prérequis

- Proxmox VE installé et accessible
- ISO Debian 12 (Bookworm) ou Ubuntu 24.04 LTS disponible dans le stockage Proxmox

---

## Création de la VM

Dans l'interface Proxmox (https://IP-PROXMOX:8006) :

### 1. Créer la VM

```
Datacenter → Nœud → Créer VM
```

**Configuration recommandée :**

| Paramètre     | Valeur recommandée           |
|---------------|------------------------------|
| Nom           | `esp32-manager`              |
| OS            | Debian 12 ou Ubuntu 24.04    |
| CPU           | 2 cœurs                      |
| RAM           | 2 Go                         |
| Disque        | 20 Go (SSD si possible)      |
| Réseau        | Bridge `vmbr0` (réseau LAN)  |

### 2. Installer le système

Démarrer la VM et suivre l'installation standard Debian/Ubuntu.

Options recommandées :
- Pas d'interface graphique (serveur uniquement)
- SSH activé
- Utilisateur non-root créé (ex : `esp32admin`)

### 3. Configurer une IP fixe

```bash
sudo nano /etc/network/interfaces
```

Exemple de configuration IP fixe :

```
auto ens18
iface ens18 inet static
  address 192.168.1.100
  netmask 255.255.255.0
  gateway 192.168.1.1
  dns-nameservers 1.1.1.1 8.8.8.8
```

Appliquer :

```bash
sudo systemctl restart networking
```

### 4. Mises à jour système

```bash
sudo apt update && sudo apt upgrade -y
```

### 5. Vérifier la connectivité

```bash
ping -c 3 1.1.1.1
ping -c 3 google.com
```

---

## Résultat attendu

La VM est accessible via SSH :

```bash
ssh esp32admin@192.168.1.100
```

**Étape suivante → [02-docker.md](./02-docker.md)**
