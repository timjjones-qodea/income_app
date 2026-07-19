# Operations and deployment

This project follows the same broad deployment pattern as MTD Bookkeeper: local development, production Docker image, GHCR registry, EdgePi deployment, Traefik routing and private Cloudflare access.

## Local development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./scripts/dev.sh
```

Open <http://127.0.0.1:8010>.

The development helper keeps dev data separate from production data and starts Uvicorn with `--reload-dir app` to avoid restart loops caused by `.venv/` or iCloud file changes.

## Local Docker

```bash
docker compose up --build
```

Use this when you want a closer-to-production runtime locally.

## Production shape

Production is defined in `docker-compose.prod.yml`.

Key properties:

- image: `ghcr.io/timjjones-qodea/income-app:latest`;
- container: `retirement-income`;
- persistent SQLite/upload data mounted under the EdgePi data root;
- no direct published host port;
- Traefik routes HTTPS traffic for `inc.braeside-host.uk`;
- service joins the shared `edge` network;
- app runs as the configured non-root UID/GID.

## First-time production setup

Create the production environment file:

```bash
cp .env.production.example .env.production
```

Create GHCR credential files:

```bash
mkdir -p .data/secrets/github
```

Add:

- `.data/secrets/github/ghcr_username`
- `.data/secrets/github/ghcr_token`

Then log in locally:

```bash
./scripts/ghcr-login.sh
```

## Deploy

```bash
./scripts/deploy.sh
```

The script:

1. validates production Compose;
2. pulls the latest Git source;
3. builds the Docker image;
4. tags and pushes to GHCR;
5. prepares EdgePi directories;
6. copies production Compose/environment files;
7. pulls the image on EdgePi;
8. recreates the container.

## Production access

The intended production URL is:

<https://inc.braeside-host.uk>

Before storing real financial data, keep the application behind Cloudflare Access or an equivalent private ingress control.

## Persistence and backups

The app stores:

- SQLite database;
- uploaded source CSVs;
- import metadata and row hashes.

Recommended backup policy:

- back up the EdgePi income data directory before significant app upgrades;
- periodically copy the SQLite database and upload directory to another machine;
- keep annual CSV report exports for tax-year review;
- before schema-changing work, take an explicit database copy.

Database migrations are not yet implemented. Schema changes currently rely on SQLAlchemy table creation and should be treated carefully once real production data exists.

## Useful checks

Local tests:

```bash
.venv/bin/pytest -q
```

Production health:

```bash
curl -I https://inc.braeside-host.uk/health
```

Docker status on EdgePi:

```bash
ssh edgepi.local docker ps
ssh edgepi.local docker logs retirement-income --tail 100
```

## Operational risks

- No authentication is built into the app itself; ingress controls matter.
- No migrations/backups workflow exists yet.
- SQLite is appropriate for this local-first use case but should be backed up.
- Manual AIC download avoids scraping, but it also means stale planning data is possible.
- Tax labels are informational until the tax engine is implemented.
