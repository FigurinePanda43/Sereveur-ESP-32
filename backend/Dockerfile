FROM python:3.12-slim

# openssh-client : nécessaire au « git fetch » de /api/system/update-check sur
# un dépôt privé accessible en SSH (la clé de déploiement est montée depuis
# l'hôte). Seul le binaire `docker` est extrait de l'archive statique : les
# plugins (compose, buildx) n'y sont pas — la mise à jour est déportée dans un
# conteneur `docker:cli` dédié, voir backend/routers/system.py.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl openssh-client \
    && curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.3.1.tgz \
    | tar xz --strip-components=1 -C /usr/local/bin docker/docker \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY frontend/ ./frontend/

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
