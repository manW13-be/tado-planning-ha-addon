FROM python:3.11-slim

# Dépendances système
RUN apt-get update \
    && apt-get install -y --no-install-recommends jq \
    && rm -rf /var/lib/apt/lists/*

# Dépendances Python
RUN pip install --no-cache-dir "python-tado>=0.18"

# Script de démarrage
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
