# Étape 2 — Installation de Docker et Docker Compose

## Prérequis

- VM Proxmox opérationnelle ([Étape 1](./01-proxmox-vm.md))
- Accès SSH avec utilisateur sudoers

---

## Installation de Docker

Se connecter à la VM :

```bash
ssh esp32admin@192.168.1.100
```

### 1. Supprimer les anciennes versions éventuelles

```bash
sudo apt remove -y docker docker-engine docker.io containerd runc 2>/dev/null || true
```

### 2. Installer les dépendances

```bash
sudo apt update
sudo apt install -y \
    ca-certificates \
    curl \
    gnupg \
    lsb-release
```

### 3. Ajouter le dépôt officiel Docker

**Pour Debian :**

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | \
    sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/debian $(lsb_release -cs) stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
```

**Pour Ubuntu :**

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
    sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
```

### 4. Installer Docker

```bash
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

### 5. Ajouter l'utilisateur au groupe Docker

```bash
sudo usermod -aG docker $USER
newgrp docker
```

### 6. Vérifier l'installation

```bash
docker --version
docker compose version
docker run hello-world
```

Résultat attendu :
```
Docker version 27.x.x, build ...
Docker Compose version v2.x.x
Hello from Docker!
```

### 7. Activer Docker au démarrage

```bash
sudo systemctl enable docker
sudo systemctl start docker
```

---

## Cloner le projet

```bash
sudo apt install -y git

git clone https://github.com/FigurinePanda43/Sereveur-ESP-32.git
cd Sereveur-ESP-32
```

---

## Résultat attendu

```bash
docker ps   # retourne la liste des conteneurs (vide pour l'instant)
ls          # montre les fichiers du projet
```

**Étape suivante → [03-cloudflare.md](./03-cloudflare.md)**
