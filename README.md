# Retirement Income Engine

Spike 1 is a local-first FastAPI application for tracing natural investment income across a couple's AJ Bell SIPPs and ISAs. It separates broker imports, market dividend data, manual assumptions, and calculated output.

## What this spike proves

- Stages AJ Bell holdings and transaction CSV files before commit.
- Detects holdings, transactions, and AIC-style dividend history from headers.
- Normalises dates, money, transaction types, tickers, names, and stable row hashes.
- Makes repeated file and row imports idempotent.
- Matches securities by ISIN, SEDOL, ticker, normalised name, then saved manual aliases.
- Calculates actual dividend and interest income by calendar and UK tax year.
- Calculates forward income using manual dividend/share, manual yield, trailing dividend events, then an explicit asset-type fallback.
- Reconciles actual receipts to dividend events with visible tolerances.
- Exports historic income by year/account/security, forward income, unmatched rows, and reconciliation as CSV.
- Preserves import-to-calculation traceability and supports import rollback.

This is analysis software, not financial advice. It deliberately excludes tax, crystallisation, withdrawal strategy, State Pension, Monte Carlo modelling, trading, and broker login.

## Run locally

Python 3.12 is recommended.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./scripts/dev.sh
```

Open <http://127.0.0.1:8010>. The database and uploaded source files are created under `data/`.

Restricting reloads to `app/` is important because the virtual environment lives inside the project directory. Without `--reload-dir app`, package installation or delayed iCloud file events under `.venv/` can trigger a restart loop.

Or use Docker:

```bash
docker compose up --build
```

Development data is isolated under `.data/dev`; production never mounts this directory. The local helper uses the same isolation and limits file watching to application code:

```bash
./scripts/dev.sh
```

## Deploy to EdgePi

Production pulls `ghcr.io/timjjones-qodea/income-app:latest`, stores persistent data under `${EDGE_DATA_ROOT}/income/data`, joins the external `edge` Docker network, and is routed by Traefik at `inc.braeside-host.uk`.
The container defaults to UID/GID `1000:1000` so the EdgePi user owns the bind-mounted SQLite data; adjust `RIE_UID` and `RIE_GID` if that host uses different IDs.

Initial setup:

```bash
cp .env.production.example .env.production
mkdir -p .data/secrets/github
# Add ghcr_username and ghcr_token files under .data/secrets/github
./scripts/ghcr-login.sh
```

Deploy:

```bash
./scripts/deploy.sh
```

The deployment pulls the latest Git revision, builds and pushes the image, synchronises the production Compose/environment files to `edgepi.local`, creates the persistent data directory, and recreates the service. Override `SERVER_FQDN`, `EDGE_NETWORK_ROOT`, `EDGE_DATA_ROOT`, `GHCR_IMAGE`, or `RIE_HOSTNAME` when required.

The production service does not publish a host port; Traefik is its only ingress. Before adding personal financial data, ensure `inc.braeside-host.uk` is covered by the same Cloudflare Access policy used for the private MTD application.

## First walkthrough

1. Open **Imports**.
2. Save each account's AIC Income Builder URL. Use **Open AIC portfolio**, press AIC's **Export current portfolio** button, then return to the app.
3. Select the intended account and upload either its AIC export or AJ Bell holdings CSV (the portfolio label `ISA` cannot identify its owner).
4. Review the normalised rows and commit the clean rows.
5. Open **Holdings** to see values and forward estimates.
6. Open **Securities** to replace the visible 4% investment-trust fallback with audited manual assumptions.
7. Upload transactions and AIC/manual dividend history, then inspect **Income** and **Reconciliation**.

Tim/Wife and one AJ Bell ISA/SIPP each are seeded as editable starter reference data. No broker credentials are used or stored.

## Supported CSV shapes

Headers are case/punctuation tolerant. The included examples document the baseline formats.

- Holdings: `Investment`, `Quantity`, `Price`, `Value (£)`, `Date`, `Ticker`; optional cost/currency/ISIN/SEDOL.
- Transactions: date, description, amount (or credit/debit); optional type/ticker/ISIN/quantity/fees/tax.
- Dividend history: ticker or ISIN, payment date, dividend per share; optional ex-date/type/source/URL.
- AIC Income Builder: `Company`, `AIC sector`, `Income received`, `Shares held`, `Div freq`, `Yield (%)`.

Dividend-per-share values must use the same currency unit as the holding price. For the supplied AJ Bell export, prices are pounds, so enter dividends in pounds (for example `0.055`, not `5.5`, for 5.5p).

## Test

```bash
.venv/bin/pytest -q
```

The tests cover the supplied CSV, classification, duplicate safety, matching hierarchy/manual aliases, calendar and UK tax years, forward-income methods, and reconciliation mismatch detection.

## Next build slice

Before production use, the next slice should add account/person administration, transaction-format fixtures from real AJ Bell exports, richer unmatched-row remediation, database migrations/backups, authentication at the deployment edge, and a reviewed AIC ingestion method. Automated AIC scraping is intentionally not included: no stable public API or permission was assumed.
