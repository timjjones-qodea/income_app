# Roadmap and development spike plan

The app should evolve from an income-analysis spike into a dependable household retirement-income planning tool.

## Product north star

The app should show the combined natural income from Tim and Wendy’s real portfolios, split by:

- person;
- account;
- tax wrapper;
- security;
- actual received income;
- repeatable/planning income;
- taxable vs non-taxable treatment;
- non-investment retirement income streams.

The aim is not to optimise trading. The aim is to make the retirement income picture visible, auditable and calm.

## Current release: usable income ledger

Already implemented:

- AJ Bell portfolio import.
- AJ Bell cash statement import.
- AIC Income Builder import.
- seeded Tim/Wendy accounts.
- current holdings and planning income view.
- actual income history.
- basic tax-treatment labels.
- manual security income assumptions.
- import staging, duplicate safety and rollback.
- GHCR/EdgePi/Traefik deployment.

## Near-term slice 1: product guidance and confidence

Goal: make the app self-explanatory.

Tasks:

- Add clear help panels to every page.
- Document exact data workflow and source priority.
- Show “next best action” when data is missing.
- Add status indicators for each account: holdings loaded, cash statement loaded, AIC loaded.
- Add “last refreshed” dates per account/source.
- Add sample screenshots or a short operator guide once the workflow settles.

## Near-term slice 2: real household income model

Goal: show the household retirement income picture, not just investment income.

Tasks:

- Add manual income streams:
  - State Pension;
  - defined benefit pensions;
  - annuities;
  - cash interest outside AJ Bell;
  - part-time/consulting income if relevant;
  - planned SIPP withdrawals.
- Add start/end dates, inflation linking and annual escalation.
- Add person ownership and tax category.
- Add combined monthly/annual household projection.
- Separate “natural income” from “planned withdrawals”.

## Near-term slice 3: tax-aware view

Goal: distinguish gross income from spendable income.

Tasks:

- Formalise account tax classes:
  - ISA tax-free;
  - SIPP pension-wrapped accumulation;
  - taxable pension withdrawal;
  - VCT dividend tax-free;
  - unwrapped taxable GIA.
- Add UK tax-year projection.
- Add dividend, savings and personal allowance modelling.
- Track taxable income by person.
- Add simple tax assumptions with manual override.
- Keep the first tax model intentionally transparent and conservative.

## Near-term slice 4: better import review

Goal: make data quality issues obvious and fixable.

Tasks:

- Add account-level import status cards.
- Add unmatched dividend remediation flow from the import detail page.
- Add bulk mapping for repeated AJ Bell dividend descriptions.
- Add filters to import history.
- Add clearer duplicate/rollback messaging.
- Add row-level source download links.

## Medium-term slice 5: scenario planning

Goal: compare retirement income scenarios without mutating source data.

Tasks:

- Add named scenarios.
- Allow yield haircut/uplift assumptions.
- Model special dividends excluded/included.
- Model SIPP drawdown strategy.
- Model cash buffer drawdown.
- Add inflation adjustment.
- Add charts for annual and monthly income.

## Medium-term slice 6: AIC workflow improvements

Goal: reduce manual friction while staying inside acceptable data-use boundaries.

Tasks:

- Keep “Open AIC portfolio” buttons for user-triggered downloads.
- Add per-account AIC freshness warnings.
- Investigate whether AIC can provide approved API/data access.
- If approval exists, add server-side scheduled refresh.
- If not, keep user-click download/import as the compliant workflow.

## Medium-term slice 7: migrations, backups and recovery

Goal: make production safe for real data.

Tasks:

- Add Alembic migrations.
- Add one-command database backup.
- Add restore instructions.
- Add pre-deploy backup hook.
- Add production smoke test after deploy.
- Add version stamping in the UI.

## Later ideas

- Broker/provider abstraction beyond AJ Bell.
- PDF/statement ingestion if CSVs become awkward.
- Monthly cash-flow calendar by expected payment month.
- Dividend cut/watchlist notes.
- Export package for accountant/tax return support.
- Household balance-sheet summary.
- Optional read-only GitHub Actions build pipeline.

## Explicit non-goals for now

- Automated broker login.
- Automated trading.
- Portfolio optimisation or buy/sell recommendations.
- Unapproved scraping.
- Complex Monte Carlo modelling before the deterministic income model is trusted.
