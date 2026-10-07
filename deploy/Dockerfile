# MGM Network Vault - Linux container
#   docker compose -f deploy/docker-compose.yml up -d --build
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git iputils-ping openssh-client ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY network_vault ./network_vault
COPY run.py manage.py config.example.toml ./

ENV NETWORK_VAULT_DATA_DIR=/data \
    NETWORK_VAULT_PORT=8080 \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/login', timeout=8)"
CMD ["python", "run.py"]
