FROM python:3.11-slim

# supercronic: container-friendly cron that logs to stdout
ARG SUPERCRONIC_VERSION=v0.2.33
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ca-certificates; \
    rm -rf /var/lib/apt/lists/*; \
    arch="$(dpkg --print-architecture)"; \
    curl -fsSLo /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${arch}"; \
    chmod +x /usr/local/bin/supercronic

WORKDIR /app
COPY pyproject.toml README.md ./
COPY curhouse ./curhouse
RUN pip install --no-cache-dir .
COPY scripts ./scripts
COPY deploy/crontab /app/crontab

CMD ["curhouse", "--help"]
