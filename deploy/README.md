# Portable deploy: Metabase + curhouse sync

Self-contained stack (Metabase + Postgres app-db + scheduled CUR sync) that talks to a
**ClickHouse already running on the host at `localhost:8123`**.

## Prerequisites
- Docker (with compose) running on the target machine.
- ClickHouse reachable at `localhost:8123` on the host, with the `default` user allowed from
  container network (host->container is a "remote" connection to ClickHouse).
- An AWS access key that can read the CUR S3 bucket.

## Setup
```bash
cp deploy/.env.example deploy/.env      # fill AWS keys + Metabase admin creds
cp config.example.toml config.toml      # fill bucket/account/region; set profile = ""
docker compose -f deploy/docker-compose.yml up -d --build
```

Order handled automatically: `metabase-db` + `metabase` start → `curhouse-sync-init` loads the
CUR history from S3 → `metabase-provision` builds the dashboard → `curhouse-sync` syncs daily at
06:00 UTC. Dashboard: <http://localhost:3000>.

## Operate
- Logs: `docker compose -f deploy/docker-compose.yml logs -f curhouse-sync`
- Manual sync now: `docker compose -f deploy/docker-compose.yml run --rm curhouse-sync-init`
- Re-provision dashboard: `docker compose -f deploy/docker-compose.yml run --rm metabase-provision`

## Notes
- Point at a non-host ClickHouse by setting `CURHOUSE_CH_HOST`/`CURHOUSE_CH_PORT` in `.env`.
- Reboot persistence is the host runtime's job (e.g. Docker Desktop "start on login", or
  `brew services start colima`). The container `restart: unless-stopped` policies then bring the
  stack back.
