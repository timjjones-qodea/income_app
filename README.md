# Retirement Income

Local-first income planning for Tim and Wendy’s AJ Bell portfolios.

The app brings together current holdings, actual dividend and interest receipts, and optional AIC Income Builder data so we can answer the practical retirement question: “what natural income are these portfolios really producing, and how much of it is taxable?”

It is analysis software, not financial advice. It does not trade, optimise, recommend purchases, log in to AJ Bell, or scrape the AIC website.

## Current purpose

The useful centre of gravity is deliberately simple:

1. Load each AJ Bell portfolio CSV to establish current holdings, quantities and market value.
2. Load each AJ Bell cash statement for the prior year to establish actual dividend and interest receipts.
3. Optionally load the matching AIC Income Builder export to create a cleaner planning baseline for investment trusts, because the AIC figure helps distinguish repeatable regular income from special dividends.
4. Review income by person, account wrapper, security and tax treatment.
5. Use the result as the base for broader retirement income projections.

The seeded accounts are:

| Person | Account | Tax interpretation used by the app |
| --- | --- | --- |
| Tim | ISA | Tax-free ISA income |
| Tim | SIPP | Pension wrapper; taxable when withdrawn, not while held |
| Tim | GIA | VCT-only general investment account; dividends treated as tax-free |
| Wendy | ISA | Tax-free ISA income |
| Wendy | SIPP | Pension wrapper; taxable when withdrawn, not while held |
| Wendy | GIA | Unwrapped taxable investment account |

Tax labels are currently descriptive. Detailed tax calculation is a planned build slice.

## What works now

- FastAPI web app with local SQLite storage.
- Separate development and production data directories.
- Docker and EdgePi deployment via GHCR, following the MTD Bookkeeper pattern.
- Traefik-ready production compose labels for `inc.braeside-host.uk`.
- Seeded Tim/Wendy AJ Bell accounts and saved AIC Income Builder URLs.
- CSV staging before commit, with row-level validation and duplicate safety.
- Import rollback for committed files.
- AJ Bell portfolio import for current holdings.
- AJ Bell cash statement import for actual dividends, interest, charges and cash movements.
- AIC Income Builder portfolio import for trailing regular income planning.
- Older generic AJ Bell transaction and dividend-event imports retained for compatibility.
- Security matching by ISIN, SEDOL, ticker, normalised name and manual alias.
- Security assumptions for manual dividend-per-share or yield overrides.
- Dashboard metrics for portfolio value, planning income and actual trailing income.
- Holdings view showing current holdings paired with the highest-priority income source.
- Income history view showing actual receipts by calendar year and UK tax year.
- Securities view for unmatched rows and manual income assumptions.
- Reconciliation view comparing actual dividends with dividend events when those events exist.
- CSV reports for income, holdings/planning income, unmatched rows and reconciliation.

## Data-source priority

Forward or planning income is calculated per current holding using this priority order:

1. Manual annual dividend per share.
2. Manual forward yield.
3. AIC Income Builder portfolio export for that account/security.
4. AJ Bell actual dividend receipts over the trailing 12 months, including specials.
5. Imported dividend events over the trailing 12 months.
6. Asset-type fallback yield.

This means AIC data is optional but useful. If loaded, it deliberately replaces the “actual receipts including specials” basis for planning, because a large special dividend should not usually be treated as repeatable retirement income.

## Recommended workflow

Repeat this for each account: Tim ISA, Tim SIPP, Tim GIA, Wendy ISA, Wendy SIPP and Wendy GIA.

1. In AJ Bell, export the current portfolio CSV for the account.
2. In the app, open **Imports**, choose the same account, drag the portfolio CSV into the upload panel, stage it, review the rows and commit it.
3. In AJ Bell, export/download the cash statement covering roughly the previous 12 months.
4. In the app, choose the same account, upload the cash statement, stage it, review it and commit it.
5. Open **Income** to confirm dated dividends and interest have appeared.
6. Open **Holdings** to confirm current quantities and forward/planning income.
7. If the account contains AIC-covered investment trusts, open the matching AIC portfolio from **Imports**, press **Export current portfolio** on the AIC site, then upload that CSV against the same account.
8. Open **Securities** to resolve any unmatched income rows or add manual assumptions where AIC/AJ Bell data is not a good planning basis.

For the most reliable projections, refresh AJ Bell holdings monthly or after any purchase, sale or transfer. Refresh cash statements monthly or quarterly. Refresh AIC Income Builder exports monthly if using them as the planning baseline.

## Run locally

Python 3.12 is recommended.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./scripts/dev.sh
```

Open <http://127.0.0.1:8010>.

The database and uploaded source files are created under `.data/dev/` when using `scripts/dev.sh`.

The development script restricts Uvicorn reloads to `app/`. This matters because the virtual environment may live inside the project directory; without `--reload-dir app`, package installs or delayed iCloud file events under `.venv/` can trigger a restart loop.

Docker is also available:

```bash
docker compose up --build
```

## Deploy to EdgePi

Production pulls `ghcr.io/timjjones-qodea/income-app:latest`, stores persistent data under `${EDGE_DATA_ROOT}/income/data`, joins the external `edge` Docker network, and is routed by Traefik at `inc.braeside-host.uk`.

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

The deployment script validates production Compose, pulls the latest source, builds and pushes the GHCR image, prepares the EdgePi data directory, synchronises production files and recreates the service.

Before adding personal financial data, ensure `inc.braeside-host.uk` is protected by Cloudflare Access or equivalent private access controls.

## Supported CSV files

Headers are case/punctuation tolerant.

| Source | Purpose | Key columns |
| --- | --- | --- |
| AJ Bell portfolio | Current holdings and market value | `Investment`, `Quantity`, `Price`, `Value (£)`, `Date`, `Ticker` |
| AJ Bell cash statement | Actual receipts and cash movements | `Date`, `Description`, `Settlement date`, `Receipt (GBP)`, `Payment (GBP)`, `Balance (GBP)` |
| AIC Income Builder portfolio | Regular trailing income planning baseline | `Company`, `AIC sector`, `Income received`, `Shares held`, `Div freq`, `Yield (%)` |
| Generic AJ Bell transactions | Compatibility import | date, description, amount or credit/debit |
| Dividend history/events | Optional reconciliation data | ticker or ISIN, payment date, dividend per share |

Dividend-per-share values must use pounds, not pence. For example, 5.5p should be entered as `0.055`.

## Reports

- `/reports/historic-income.csv`
- `/reports/historic-income-by-account.csv`
- `/reports/historic-income-by-security.csv`
- `/reports/forward-income.csv`
- `/reports/unmatched-securities.csv`
- `/reports/dividend-reconciliation.csv`

## Tests

```bash
.venv/bin/pytest -q
```

The tests cover import detection, normalisation, duplicate safety, account seeding, security matching, UK tax years, forward income calculation and reconciliation mismatch detection.

## More documentation

- [Data workflow](docs/DATA_WORKFLOW.md)
- [Operations and deployment](docs/OPERATIONS.md)
- [Roadmap](docs/ROADMAP.md)
