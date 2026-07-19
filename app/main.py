from __future__ import annotations

import csv
import io
import json
from calendar import month_abbr
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db, init_db
from app.importers import extract_account_code
from app.models import (
    Account,
    AicPortfolioIncomeSnapshot,
    HoldingSnapshot,
    ImportJob,
    ImportRow,
    Person,
    Security,
    SecurityIncomeAssumption,
    Transaction,
)
from app.security_matching import match_security, save_manual_mapping
from app.services import (
    ImportErrorDetail,
    SECURITY_MATCH_REQUIRED_TYPES,
    aggregate_income,
    commit_import,
    create_import_job,
    current_holdings,
    forward_income_rows,
    historic_income_rows,
    reconciliation_rows,
    rollback_import,
    uk_tax_year,
)


AIC_INCOME_BUILDER_BASE_URL = "https://www.theaic.co.uk/income-finder/income-builder"
AIC_PORTFOLIO_IDS = {
    "Tim ISA": "40171",  # Tim NISA on AIC
    "Tim SIPP": "42759",
    "Tim GIA": "43171",  # Tim VCT on AIC
    "Wendy ISA": "40169",  # Wendy NISA on AIC
    "Wendy SIPP": "42717",
    "Wendy GIA": "42743",  # Wendy Equity on AIC
}

CHART_PALETTE = (
    "#0b6374", "#4d8d23", "#6f99bf", "#d85a10", "#b91f33", "#4a4a4a",
    "#e02f3d", "#83a8c9", "#8aa871", "#f08b56", "#9d9d9d", "#6b9f3a",
    "#c8631b", "#2f7686", "#c04f61", "#7894ad", "#5c7d32", "#b8792e",
)


def seed_vanguard_money_market_security(db: Session) -> None:
    security = db.scalar(select(Security).where(Security.ticker == "VASSTAI"))
    if not security:
        security = db.scalar(
            select(Security).where(Security.name == "Vanguard Sterling Short-Term Money Market Fund")
        )
    if not security:
        security = Security(
            name="Vanguard Sterling Short-Term Money Market Fund",
            ticker="VASSTAI",
            currency="GBP",
            asset_type="Fund",
            sector="Money Market",
        )
        db.add(security)
        db.flush()
    else:
        security.name = "Vanguard Sterling Short-Term Money Market Fund"
        security.ticker = security.ticker or "VASSTAI"
        security.asset_type = security.asset_type if security.asset_type != "Other" else "Fund"
        security.sector = security.sector or "Money Market"

    for external_name in (
        "Vanguard Sterling Short-Term Money Market Fund",
        "Vanguard Sterling Short Term Money Market",
        "Vanguard Sterling Short-Term Money Market",
        "Vanguard Stlg S/T Mny Mkts A GBP Acc",
        "Vanguard Stlg S/T Mny Mkts A GBP",
        "Vanguard Stlg S/T Mny Mkts",
        "VANGUARD INVESTMENTS MONEY MKT FDS VANGUARD STERLING SHORT-TERM MONEY MARKET FUND",
        "VANGUARD INVESTMENTS MONEY MKT FDS VANGUARD STERLING SHORT TERM MONEY MARKET FUND",
        "VANGUARD INVESTMENTS MONEY MKT FDS VANGUARD STERLING SHORT-TERM MONEY MARKET",
        "VANGUARD INVESTMENTS MONEY MKT FDS VANGUARD STERLING SHORT TERM MONEY MARKET",
        "Dividend Grp 1 1148.34240 VANGUARD INVESTMENTS MONEY MKT FDS VANGUARD STERLING SHORT-TERM MONEY MARKET FUND",
        "Dividend Grp 2 1148.34240 VANGUARD INVESTMENTS MONEY MKT FDS VANGUARD STERLING SHORT-TERM MONEY MARKET FUND",
        "Purchase 236,116.3582 Vanguard Stlg S/T Mny Mkts A GBP Acc",
        "Sale 75,060.987 Vanguard Stlg S/T Mny Mkts A GBP Acc",
        "Dividend 236116.3582 Vanguard Stlg S/T Mny Mkts A GBP Acc",
        "Income Payment 236116.3582 Vanguard Stlg S/T Mny Mkts A GBP Acc",
        "Distribution 236116.3582 Vanguard Stlg S/T Mny Mkts A GBP Acc",
    ):
        save_manual_mapping(db, external_name, security.id)


def match_transaction_security(db: Session, description: str) -> Security | None:
    normalized_description = " ".join((description or "").upper().split())
    if "VANGUARD INVESTMENTS MONEY MKT FDS" in normalized_description:
        seed_vanguard_money_market_security(db)
        return db.scalar(select(Security).where(Security.ticker == "VASSTAI"))
    return match_security(db, name=description)


def seed_reference_data(db: Session) -> None:
    wife = db.scalar(select(Person).where(Person.name == "Wife"))
    wendy = db.scalar(select(Person).where(Person.name == "Wendy"))
    if wife and not wendy:
        wife.name = "Wendy"
        wendy = wife
        db.flush()
    elif wife and wendy and wife.id != wendy.id:
        for account in wife.accounts:
            account.owner = wendy
        db.delete(wife)
        db.flush()

    people = {}
    for name in ("Tim", "Wendy"):
        person = db.scalar(select(Person).where(Person.name == name))
        if not person:
            person = Person(name=name, tax_residency="UK")
            db.add(person)
            db.flush()
        people[name] = person

    for old_name, new_name in (("Wife ISA", "Wendy ISA"), ("Wife SIPP", "Wendy SIPP")):
        legacy = db.scalar(select(Account).where(Account.account_name == old_name))
        replacement = db.scalar(select(Account).where(Account.account_name == new_name))
        if legacy and not replacement:
            legacy.account_name = new_name
            legacy.owner_person_id = people["Wendy"].id
            db.flush()

    account_specs = (
        ("Tim", "ISA", "Tax-free ISA", None),
        ("Tim", "SIPP", "Pension wrapper", None),
        (
            "Tim",
            "GIA",
            "VCT dividends treated as tax-free",
            "VCT-only general investment account.",
        ),
        ("Wendy", "ISA", "Tax-free ISA", None),
        ("Wendy", "SIPP", "Pension wrapper", None),
        (
            "Wendy",
            "GIA",
            "Unwrapped taxable investment account",
            "General investment account outside a tax wrapper.",
        ),
    )
    for owner, wrapper, tax_treatment, notes in account_specs:
        account_name = f"{owner} {wrapper}"
        account = db.scalar(select(Account).where(Account.account_name == account_name))
        if not account:
            account = Account(
                provider="AJ Bell",
                account_name=account_name,
                owner_person_id=people[owner].id,
                wrapper_type=wrapper,
                currency="GBP",
            )
            db.add(account)
        account.owner_person_id = people[owner].id
        account.tax_treatment = tax_treatment
        if notes and not account.notes:
            account.notes = notes
        direct_aic_url = (
            f"{AIC_INCOME_BUILDER_BASE_URL}/{AIC_PORTFOLIO_IDS[account_name]}"
        )
        old_shared_url = f"{AIC_INCOME_BUILDER_BASE_URL}/42759"
        if not account.aic_portfolio_url or (
            account_name != "Tim SIPP"
            and account.aic_portfolio_url.rstrip("/") == old_shared_url
        ):
            account.aic_portfolio_url = direct_aic_url
    seed_vanguard_money_market_security(db)
    db.commit()


def seed() -> None:
    with SessionLocal() as db:
        seed_reference_data(db)
        rematch_unmatched_transactions(db)


def rematch_unmatched_transactions(db: Session) -> int:
    fixed = 0
    affected_job_ids: set[int] = set()
    transactions = db.scalars(
        select(Transaction).where(
            Transaction.security_id.is_(None),
            Transaction.transaction_type.in_(SECURITY_MATCH_REQUIRED_TYPES),
        )
    ).all()
    for transaction in transactions:
        security = match_transaction_security(db, transaction.description)
        if not security:
            continue
        transaction.security_id = security.id
        fixed += 1
        row = db.scalar(
            select(ImportRow).where(ImportRow.row_hash == transaction.source_row_hash)
        )
        if row and row.warnings and (
            row.warnings == "Dividend security was not matched to the imported portfolio"
            or "security match may need review" in row.warnings
            or "security was not matched" in row.warnings
        ):
            row.warnings = None
            affected_job_ids.add(row.import_job_id)

    for job_id in affected_job_ids:
        job = db.get(ImportJob, job_id)
        if job:
            job.warning_count = int(sum(1 for row in job.rows if row.warnings))

    if fixed:
        db.commit()
    return fixed


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    seed()
    yield


app = FastAPI(title="Retirement Income Engine", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


def money(value) -> str:
    return f"£{Decimal(value or 0):,.2f}"


def percent(value) -> str:
    return f"{Decimal(value or 0) * 100:.2f}%"


templates.env.filters["money"] = money
templates.env.filters["percent"] = percent


def render(request: Request, name: str, **context):
    return templates.TemplateResponse(
        request=request,
        name=name,
        context={"request": request, "today": date.today(), **context},
    )


@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    holdings = current_holdings(db)
    forward = forward_income_rows(db)
    historic = historic_income_rows(db)
    current_year = date.today().year
    last_calendar = current_year - 1
    current_tax_start = date.today().year if (date.today().month, date.today().day) >= (4, 6) else date.today().year - 1
    last_tax = f"{current_tax_start - 1}/{str(current_tax_start)[-2:]}"
    value = sum((Decimal(item.market_value) for item in holdings), Decimal("0"))
    forward_total = sum((item["forward_income"] for item in forward), Decimal("0"))
    trailing_cutoff = date.today() - timedelta(days=365)
    trailing = [
        item
        for item in historic
        if trailing_cutoff <= item["transaction"].transaction_date <= date.today()
    ]
    trailing_dividends = sum((item["dividends"] for item in trailing), Decimal("0"))
    trailing_interest = sum((item["interest"] for item in trailing), Decimal("0"))
    trailing_total = trailing_dividends + trailing_interest
    metrics = {
        "portfolio_value": value,
        "historic_calendar": sum(
            (item["total"] for item in historic if item["calendar_year"] == last_calendar),
            Decimal("0"),
        ),
        "historic_tax": sum(
            (item["total"] for item in historic if item["tax_year"] == last_tax), Decimal("0")
        ),
        "forward_income": forward_total,
        "income_yield": forward_total / value if value else Decimal("0"),
        "trailing_income": trailing_total,
        "trailing_dividends": trailing_dividends,
        "trailing_interest": trailing_interest,
        "trailing_yield": trailing_total / value if value else Decimal("0"),
        "actual_difference": trailing_total - forward_total,
        "unmatched": int(
            db.scalar(
                select(func.count()).select_from(Transaction).where(
                    Transaction.security_id.is_(None),
                    Transaction.transaction_type.in_(SECURITY_MATCH_REQUIRED_TYPES),
                )
            )
            or 0
        ),
        "warnings": int(
            db.scalar(select(func.sum(ImportJob.warning_count)).where(ImportJob.status != "ROLLED_BACK"))
            or 0
        ),
        "last_calendar": last_calendar,
        "last_tax": last_tax,
    }
    by_account = {}
    for row in forward:
        by_account.setdefault(row["account"].account_name, Decimal("0"))
        by_account[row["account"].account_name] += row["forward_income"]
    return render(request, "dashboard.html", metrics=metrics, by_account=by_account)


@app.get("/help", response_class=HTMLResponse)
def help_page(request: Request, db: Session = Depends(get_db)):
    accounts = db.scalars(select(Account).order_by(Account.account_name)).all()
    return render(request, "help.html", accounts=accounts)


@app.get("/imports", response_class=HTMLResponse)
def imports_page(request: Request, db: Session = Depends(get_db)):
    jobs = db.scalars(select(ImportJob).order_by(ImportJob.id.desc())).all()
    accounts = db.scalars(select(Account).order_by(Account.account_name)).all()
    people = db.scalars(select(Person).order_by(Person.name)).all()
    return render(request, "imports.html", jobs=jobs, accounts=accounts, people=people)


@app.get("/imports/warnings", response_class=HTMLResponse)
def import_warnings_page(request: Request, db: Session = Depends(get_db)):
    warning_rows = db.scalars(
        select(ImportRow)
        .join(ImportRow.job)
        .where(ImportRow.warnings.is_not(None), ImportJob.status != "ROLLED_BACK")
        .order_by(ImportRow.id.desc())
    ).all()
    rows = []
    for row in warning_rows:
        normalized = json.loads(row.normalized_json or "{}")
        rows.append(
            {
                "row": row,
                "description": normalized.get("description") or normalized.get("name") or "",
            }
        )
    return render(request, "import_warnings.html", rows=rows)


@app.post("/accounts")
def create_account(
    account_name: str = Form(...),
    owner_person_id: int = Form(...),
    wrapper_type: str = Form(...),
    aic_portfolio_url: str = Form(""),
    tax_treatment: str = Form(""),
    db: Session = Depends(get_db),
):
    if db.scalar(select(Account).where(Account.account_name == account_name.strip())):
        raise HTTPException(400, "An account with that name already exists")
    account = Account(
        provider="AJ Bell",
        account_name=account_name.strip(),
        owner_person_id=owner_person_id,
        wrapper_type=wrapper_type.strip().upper(),
        currency="GBP",
        tax_treatment=tax_treatment.strip() or None,
    )
    db.add(account)
    db.flush()
    if aic_portfolio_url.strip():
        account.aic_portfolio_url = validate_aic_url(aic_portfolio_url)
    db.commit()
    return RedirectResponse("/imports", status_code=303)


def validate_aic_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or parsed.hostname not in {"www.theaic.co.uk", "theaic.co.uk"}:
        raise HTTPException(400, "Enter an https://www.theaic.co.uk portfolio URL")
    if not parsed.path.startswith("/income-finder/income-builder/"):
        raise HTTPException(400, "This is not an AIC Income Builder portfolio URL")
    return value.strip()


@app.post("/accounts/{account_id}/aic-url")
def save_aic_url(
    account_id: int,
    aic_portfolio_url: str = Form(...),
    db: Session = Depends(get_db),
):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    account.aic_portfolio_url = validate_aic_url(aic_portfolio_url)
    db.commit()
    return RedirectResponse("/imports", status_code=303)


@app.post("/imports/upload")
async def upload_import(
    file: UploadFile = File(...),
    account_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    content = await file.read()
    try:
        job = create_import_job(db, file.filename or "upload.csv", content, account_id)
    except ImportErrorDetail as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(f"/imports/{job.id}", status_code=303)


@app.get("/imports/{job_id}", response_class=HTMLResponse)
def import_detail(job_id: int, request: Request, db: Session = Depends(get_db)):
    job = db.get(ImportJob, job_id)
    if not job:
        raise HTTPException(404, "Import not found")
    return render(request, "import_detail.html", job=job)


@app.post("/imports/{job_id}/commit")
def import_commit(job_id: int, db: Session = Depends(get_db)):
    job = db.get(ImportJob, job_id)
    if not job:
        raise HTTPException(404, "Import not found")
    try:
        commit_import(db, job)
    except ImportErrorDetail as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(f"/imports/{job_id}", status_code=303)


@app.post("/imports/{job_id}/rollback")
def import_rollback(job_id: int, db: Session = Depends(get_db)):
    job = db.get(ImportJob, job_id)
    if not job:
        raise HTTPException(404, "Import not found")
    try:
        rollback_import(db, job)
    except ImportErrorDetail as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(f"/imports/{job_id}", status_code=303)


def holding_account_summaries(db: Session, rows: list[dict]) -> list[dict]:
    accounts = db.scalars(select(Account).order_by(Account.account_name)).all()
    rows_by_account: dict[int, list[dict]] = {account.id: [] for account in accounts}
    for row in rows:
        rows_by_account.setdefault(row["account"].id, []).append(row)
    account_summaries = []
    for account in accounts:
        account_rows = sorted(
            rows_by_account.get(account.id, []),
            key=lambda item: (
                item["security"].name.upper(),
                item["security"].ticker or "",
            ),
        )
        value = sum((row["value"] for row in account_rows), Decimal("0"))
        income = sum((row["forward_income"] for row in account_rows), Decimal("0"))
        sources = sorted({row["source"] for row in account_rows})
        account_summaries.append(
            {
                "account": account,
                "rows": account_rows,
                "security_count": len(account_rows),
                "value": value,
                "income": income,
                "income_yield": income / value if value else Decimal("0"),
                "sources": sources,
                "has_fallback": any("fallback" in row["source"].lower() for row in account_rows),
            }
        )
    return account_summaries


def household_holding_breakdowns(rows: list[dict]) -> dict[str, list[dict]]:
    by_asset_type: dict[str, dict] = {}
    by_security: dict[int, dict] = {}
    for row in rows:
        security = row["security"]
        asset_key = security.asset_type or "Other"
        asset_target = by_asset_type.setdefault(
            asset_key,
            {
                "asset_type": asset_key,
                "security_ids": set(),
                "account_ids": set(),
                "positions": 0,
                "value": Decimal("0"),
                "income": Decimal("0"),
            },
        )
        asset_target["security_ids"].add(security.id)
        asset_target["account_ids"].add(row["account"].id)
        asset_target["positions"] += 1
        asset_target["value"] += row["value"]
        asset_target["income"] += row["forward_income"]

        security_target = by_security.setdefault(
            security.id,
            {
                "security": security,
                "account_ids": set(),
                "positions": 0,
                "quantity": Decimal("0"),
                "value": Decimal("0"),
                "income": Decimal("0"),
            },
        )
        security_target["account_ids"].add(row["account"].id)
        security_target["positions"] += 1
        security_target["quantity"] += row["quantity"]
        security_target["value"] += row["value"]
        security_target["income"] += row["forward_income"]

    asset_rows = []
    for item in by_asset_type.values():
        value = item["value"]
        asset_rows.append(
            {
                **item,
                "security_count": len(item["security_ids"]),
                "account_count": len(item["account_ids"]),
                "income_yield": item["income"] / value if value else Decimal("0"),
            }
        )
    security_rows = []
    for item in by_security.values():
        value = item["value"]
        security_rows.append(
            {
                **item,
                "account_count": len(item["account_ids"]),
                "income_yield": item["income"] / value if value else Decimal("0"),
            }
        )
    return {
        "asset_types": sorted(asset_rows, key=lambda item: item["value"], reverse=True),
        "securities": sorted(security_rows, key=lambda item: item["value"], reverse=True),
    }


def shift_month(value: date, offset: int) -> date:
    index = value.year * 12 + value.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


def last_complete_month_window(today: date) -> tuple[date, date, list[date]]:
    current_month = date(today.year, today.month, 1)
    last_month = shift_month(current_month, -1)
    months = [shift_month(last_month, offset) for offset in range(-11, 1)]
    end = shift_month(last_month, 1) - timedelta(days=1)
    return months[0], end, months


def latest_aic_snapshots(db: Session) -> list[AicPortfolioIncomeSnapshot]:
    snapshots = db.scalars(
        select(AicPortfolioIncomeSnapshot)
        .join(ImportJob, AicPortfolioIncomeSnapshot.source_import_id == ImportJob.id)
        .where(ImportJob.status != "ROLLED_BACK")
        .order_by(
            AicPortfolioIncomeSnapshot.account_id,
            AicPortfolioIncomeSnapshot.security_id,
            AicPortfolioIncomeSnapshot.snapshot_date.desc(),
            AicPortfolioIncomeSnapshot.id.desc(),
        )
    ).all()
    latest: dict[tuple[int, int], AicPortfolioIncomeSnapshot] = {}
    for snapshot in snapshots:
        latest.setdefault((snapshot.account_id, snapshot.security_id), snapshot)
    return list(latest.values())


def aic_visualisation_context(
    db: Session,
    account_id: int | None,
    breakdown: str,
) -> dict:
    accounts = db.scalars(select(Account).order_by(Account.account_name)).all()
    account_lookup = {account.id: account for account in accounts}
    selected_account = account_lookup.get(account_id) if account_id else None
    start, end, months = last_complete_month_window(date.today())

    actual_rows = [
        row for row in historic_income_rows(db)
        if start <= row["transaction"].transaction_date <= end
        and (not account_id or row["account"].id == account_id)
    ]
    aic_snapshots = [
        snapshot for snapshot in latest_aic_snapshots(db)
        if not account_id or snapshot.account_id == account_id
    ]

    component_key = "account" if breakdown == "account" else "security"
    component_totals: dict[str, Decimal] = {}
    month_totals: dict[date, Decimal] = {month: Decimal("0") for month in months}
    month_components: dict[date, dict[str, Decimal]] = {month: {} for month in months}
    for row in actual_rows:
        month = date(row["transaction"].transaction_date.year, row["transaction"].transaction_date.month, 1)
        if month not in month_totals:
            continue
        if component_key == "account":
            label = row["account"].account_name
        else:
            label = row["security"].ticker if row["security"] and row["security"].ticker else (
                row["security"].name if row["security"] else "Unmatched"
            )
        value = row["total"]
        month_totals[month] += value
        month_components[month][label] = month_components[month].get(label, Decimal("0")) + value
        component_totals[label] = component_totals.get(label, Decimal("0")) + value

    ordered_components = [
        label for label, _amount in sorted(
            component_totals.items(), key=lambda item: item[1], reverse=True
        )
    ]
    color_by_component = {
        label: CHART_PALETTE[index % len(CHART_PALETTE)]
        for index, label in enumerate(ordered_components)
    }
    max_month_total = max(month_totals.values() or [Decimal("0")])
    chart_months = []
    plot_width = 1080
    plot_height = 300
    plot_left = 56
    plot_top = 24
    slot_width = plot_width / len(months)
    bar_width = slot_width * 0.64
    baseline = plot_top + plot_height
    for month in months:
        total = month_totals[month]
        segments = []
        y_cursor = baseline
        for label in ordered_components:
            amount = month_components[month].get(label, Decimal("0"))
            if not amount or not max_month_total:
                continue
            segment_height = float((amount / max_month_total) * Decimal(str(plot_height)))
            y_cursor -= segment_height
            segments.append(
                {
                    "label": label,
                    "amount": amount,
                    "height": segment_height,
                    "y": y_cursor,
                    "color": color_by_component[label],
                }
            )
        index = len(chart_months)
        chart_months.append(
            {
                "month": month,
                "label": month_abbr[month.month],
                "total": total,
                "segments": segments,
                "x": plot_left + (slot_width * index) + ((slot_width - bar_width) / 2),
                "label_x": plot_left + (slot_width * index) + (slot_width / 2),
                "bar_width": bar_width,
            }
        )
    axis_values = []
    if max_month_total:
        for fraction in (Decimal("1"), Decimal("0.5"), Decimal("0")):
            value = (max_month_total * fraction).quantize(Decimal("0.01"))
            axis_values.append(
                {
                    "value": value,
                    "y": plot_top + float((Decimal("1") - fraction) * Decimal(str(plot_height))),
                }
            )

    monthly_table_rows = []
    for label in ordered_components:
        amounts = [month_components[month].get(label, Decimal("0")) for month in months]
        monthly_table_rows.append(
            {
                "label": label,
                "color": color_by_component[label],
                "amounts": amounts,
                "total": sum(amounts, Decimal("0")),
            }
        )

    aic_by_security: dict[int, dict] = {}
    for snapshot in aic_snapshots:
        target = aic_by_security.setdefault(
            snapshot.security_id,
            {
                "security": snapshot.security,
                "sector": snapshot.aic_sector or snapshot.security.sector,
                "dividend_frequency": snapshot.dividend_frequency,
                "accounts": set(),
                "shares_held": Decimal("0"),
                "income_received": Decimal("0"),
                "trailing_yield_values": [],
            },
        )
        target["accounts"].add(snapshot.account.account_name)
        target["shares_held"] += Decimal(snapshot.shares_held)
        target["income_received"] += Decimal(snapshot.income_received)
        if snapshot.trailing_yield is not None:
            target["trailing_yield_values"].append(Decimal(snapshot.trailing_yield))

    aic_components = []
    for item in aic_by_security.values():
        yields = item["trailing_yield_values"]
        trailing_yield = sum(yields, Decimal("0")) / Decimal(len(yields)) if yields else None
        aic_components.append(
            {
                **item,
                "account_count": len(item["accounts"]),
                "account_names": ", ".join(sorted(item["accounts"])),
                "trailing_yield": trailing_yield,
            }
        )
    aic_components.sort(key=lambda item: item["income_received"], reverse=True)

    actual_dividends = sum((row["dividends"] for row in actual_rows), Decimal("0"))
    actual_interest = sum((row["interest"] for row in actual_rows), Decimal("0"))
    actual_total = actual_dividends + actual_interest
    aic_total = sum((item["income_received"] for item in aic_components), Decimal("0"))
    months_without_actual = sum(1 for month in months if month_totals[month] == 0)

    return {
        "accounts": accounts,
        "selected_account": selected_account,
        "selected_account_id": account_id or "",
        "breakdown": component_key,
        "period_label": f"{month_abbr[start.month]} {start.year} – {month_abbr[end.month]} {end.year}",
        "chart_months": chart_months,
        "chart_axis": axis_values,
        "chart_width": plot_left + plot_width + 22,
        "chart_height": baseline + 44,
        "chart_plot_left": plot_left,
        "chart_plot_top": plot_top,
        "chart_plot_width": plot_width,
        "chart_plot_height": plot_height,
        "chart_baseline": baseline,
        "monthly_table_rows": monthly_table_rows,
        "monthly_table_totals": [month_totals[month] for month in months],
        "aic_components": aic_components,
        "actual_total": actual_total,
        "actual_dividends": actual_dividends,
        "actual_interest": actual_interest,
        "aic_total": aic_total,
        "aic_monthly_average": aic_total / Decimal("12") if aic_total else Decimal("0"),
        "aic_gap": aic_total - actual_total,
        "months_without_actual": months_without_actual,
        "component_legend": [
            {
                "label": label,
                "color": color_by_component[label],
                "amount": component_totals[label],
            }
            for label in ordered_components[:18]
        ],
    }


@app.get("/holdings", response_class=HTMLResponse)
def holdings_page(request: Request, db: Session = Depends(get_db)):
    rows = forward_income_rows(db)
    account_summaries = holding_account_summaries(db, rows)
    return render(
        request,
        "holdings.html",
        rows=rows,
        account_summaries=account_summaries,
        household_breakdowns=household_holding_breakdowns(rows),
        total_value=sum((row["value"] for row in rows), Decimal("0")),
        total_income=sum((row["forward_income"] for row in rows), Decimal("0")),
    )


@app.post("/holdings/accounts/{account_id}/delete-holdings")
def delete_account_holdings(account_id: int, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    db.execute(delete(HoldingSnapshot).where(HoldingSnapshot.account_id == account_id))
    db.commit()
    return RedirectResponse("/holdings", status_code=303)


@app.get("/income/history", response_class=HTMLResponse)
def income_history(
    request: Request,
    person: str | None = None,
    account: str | None = None,
    wrapper: str | None = None,
    security: str | None = None,
    period: str | None = None,
    calendar_year: int | None = None,
    tax_year: str | None = None,
    db: Session = Depends(get_db),
):
    all_rows = historic_income_rows(db)
    available_calendar_years = sorted({row["calendar_year"] for row in all_rows}, reverse=True)
    available_tax_years = sorted({row["tax_year"] for row in all_rows}, reverse=True)
    if period not in {"trailing", "calendar_year", "tax_year"}:
        if calendar_year:
            period = "calendar_year"
        elif tax_year:
            period = "tax_year"
        else:
            period = "trailing"
    if period == "calendar_year" and calendar_year is None and available_calendar_years:
        calendar_year = available_calendar_years[0]
    if period == "tax_year" and not tax_year and available_tax_years:
        tax_year = available_tax_years[0]

    rows = list(all_rows)
    if person:
        rows = [row for row in rows if row["person"].name == person]
    if account:
        rows = [row for row in rows if row["account"].account_name == account]
    if wrapper:
        rows = [row for row in rows if row["account"].wrapper_type == wrapper]
    if security:
        rows = [row for row in rows if row["security"] and row["security"].ticker == security]
    if period == "trailing":
        trailing_cutoff = date.today() - timedelta(days=365)
        rows = [
            row
            for row in rows
            if trailing_cutoff <= row["transaction"].transaction_date <= date.today()
        ]
        period_label = "Last 12 months"
    elif period == "calendar_year" and calendar_year:
        rows = [row for row in rows if row["calendar_year"] == calendar_year]
        period_label = str(calendar_year)
    elif period == "tax_year" and tax_year:
        rows = [row for row in rows if row["tax_year"] == tax_year]
        period_label = tax_year
    else:
        period_label = "All income"

    for row in rows:
        row["summary_period"] = period_label
    annual = aggregate_income(rows, ("summary_period", "person", "account"))
    summary = {
        "dividends": sum((row["dividends"] for row in rows), Decimal("0")),
        "interest": sum((row["interest"] for row in rows), Decimal("0")),
        "total": sum((row["total"] for row in rows), Decimal("0")),
    }
    forward = forward_income_rows(db)
    forward_total = sum((item["forward_income"] for item in forward), Decimal("0"))
    current_value = sum((Decimal(item.market_value) for item in current_holdings(db)), Decimal("0"))
    return render(
        request,
        "income.html",
        rows=rows,
        annual=annual,
        summary=summary,
        period=period,
        period_label=period_label,
        calendar_year=calendar_year,
        tax_year=tax_year,
        available_calendar_years=available_calendar_years,
        available_tax_years=available_tax_years,
        planning_income=forward_total,
        actual_vs_planning=summary["total"] - forward_total,
        actual_yield=summary["total"] / current_value if current_value else Decimal("0"),
    )


@app.get("/income/visualisation", response_class=HTMLResponse)
def income_visualisation(
    request: Request,
    account_id: int | None = None,
    breakdown: str = "security",
    db: Session = Depends(get_db),
):
    return render(
        request,
        "income_visualisation.html",
        **aic_visualisation_context(db, account_id, breakdown),
    )


@app.get("/income/forward", response_class=HTMLResponse)
def forward_income(request: Request, db: Session = Depends(get_db)):
    rows = forward_income_rows(db)
    return render(
        request,
        "holdings.html",
        rows=rows,
        account_summaries=holding_account_summaries(db, rows),
        household_breakdowns=household_holding_breakdowns(rows),
        total_value=sum((row["value"] for row in rows), Decimal("0")),
        total_income=sum((row["forward_income"] for row in rows), Decimal("0")),
        title="Forward income",
    )


@app.get("/securities", response_class=HTMLResponse)
def securities_page(request: Request, db: Session = Depends(get_db)):
    seed_vanguard_money_market_security(db)
    rematch_unmatched_transactions(db)
    securities = db.scalars(select(Security).order_by(Security.name)).all()
    assumptions = db.scalars(
        select(SecurityIncomeAssumption)
        .where(SecurityIncomeAssumption.active.is_(True))
        .order_by(SecurityIncomeAssumption.assumption_date.desc())
    ).all()
    assumption_by_security = {}
    for item in assumptions:
        assumption_by_security.setdefault(item.security_id, item)
    unmatched = db.scalars(
        select(Transaction)
        .where(
            Transaction.security_id.is_(None),
            Transaction.transaction_type.in_(SECURITY_MATCH_REQUIRED_TYPES),
        )
        .order_by(Transaction.id.desc())
    ).all()
    return render(
        request,
        "securities.html",
        securities=securities,
        assumptions=assumption_by_security,
        unmatched=unmatched,
    )


@app.get("/securities/unmatched", response_class=HTMLResponse)
def securities_unmatched(request: Request, db: Session = Depends(get_db)):
    return securities_page(request, db)


@app.post("/securities/rematch")
def rematch_securities(db: Session = Depends(get_db)):
    seed_vanguard_money_market_security(db)
    rematch_unmatched_transactions(db)
    return RedirectResponse("/securities#unmatched", status_code=303)


@app.post("/securities/{security_id}/map")
def map_security(
    security_id: int,
    external_name: str = Form(...),
    source: str = Form("AJ Bell"),
    db: Session = Depends(get_db),
):
    if not db.get(Security, security_id):
        raise HTTPException(404, "Security not found")
    save_manual_mapping(db, external_name, security_id, source)
    for transaction in db.scalars(
        select(Transaction).where(
            Transaction.security_id.is_(None), Transaction.description == external_name
        )
    ):
        transaction.security_id = security_id
    db.commit()
    return RedirectResponse("/securities", status_code=303)


@app.post("/securities/{security_id}/assumption")
def save_assumption(
    security_id: int,
    forward_annual_dividend_per_share: str = Form(""),
    forward_yield_percent: str = Form(""),
    dividend_growth_percent: str = Form(""),
    confidence: str = Form("Medium"),
    source: str = Form("Manual"),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    if not db.get(Security, security_id):
        raise HTTPException(404, "Security not found")
    for item in db.scalars(
        select(SecurityIncomeAssumption).where(
            SecurityIncomeAssumption.security_id == security_id,
            SecurityIncomeAssumption.active.is_(True),
        )
    ):
        item.active = False
    def decimal_or_none(value: str, divisor: Decimal | None = None):
        if not value.strip():
            return None
        result = Decimal(value.strip())
        return result / divisor if divisor else result
    db.add(
        SecurityIncomeAssumption(
            security_id=security_id,
            assumption_date=date.today(),
            forward_annual_dividend_per_share=decimal_or_none(
                forward_annual_dividend_per_share
            ),
            forward_yield=decimal_or_none(forward_yield_percent, Decimal("100")),
            dividend_growth_rate=decimal_or_none(
                dividend_growth_percent, Decimal("100")
            ),
            source=source,
            confidence=confidence,
            notes=notes or None,
            active=True,
        )
    )
    db.commit()
    return RedirectResponse("/securities", status_code=303)


@app.get("/reconciliation", response_class=HTMLResponse)
def reconciliation(request: Request, db: Session = Depends(get_db)):
    return render(request, "reconciliation.html", rows=reconciliation_rows(db))


def csv_response(filename: str, headers: list[str], rows: list[list]) -> Response:
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows(rows)
    return Response(
        stream.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/reports/historic-income.csv")
def report_historic_income(db: Session = Depends(get_db)):
    rows = historic_income_rows(db)
    grouped = {}
    for row in rows:
        key = (row["calendar_year"], row["tax_year"])
        grouped[key] = grouped.get(key, Decimal("0")) + row["total"]
    return csv_response(
        "historic_income_by_year.csv",
        ["calendar_year", "tax_year", "natural_income"],
        [[calendar_year, tax_year, amount] for (calendar_year, tax_year), amount in sorted(grouped.items())],
    )


@app.get("/reports/historic-income-by-account.csv")
def report_historic_income_by_account(db: Session = Depends(get_db)):
    grouped = {}
    for row in historic_income_rows(db):
        key = (
            row["calendar_year"],
            row["person"].name,
            row["account"].account_name,
            row["account"].wrapper_type,
        )
        target = grouped.setdefault(
            key, {"dividends": Decimal("0"), "interest": Decimal("0")}
        )
        target["dividends"] += row["dividends"]
        target["interest"] += row["interest"]
    return csv_response(
        "historic_income_by_account.csv",
        ["calendar_year", "person", "account", "wrapper", "dividends", "interest", "total"],
        [
            [*key, amounts["dividends"], amounts["interest"], amounts["dividends"] + amounts["interest"]]
            for key, amounts in sorted(grouped.items())
        ],
    )


@app.get("/reports/historic-income-by-security.csv")
def report_historic_income_by_security(db: Session = Depends(get_db)):
    grouped = {}
    for row in historic_income_rows(db):
        label = (
            row["security"].ticker or row["security"].name
            if row["security"]
            else "Unmatched"
        )
        key = (row["calendar_year"], label)
        grouped[key] = grouped.get(key, Decimal("0")) + row["total"]
    return csv_response(
        "historic_income_by_security.csv",
        ["calendar_year", "security", "natural_income"],
        [[*key, amount] for key, amount in sorted(grouped.items())],
    )


@app.get("/reports/forward-income.csv")
def report_forward_income(db: Session = Depends(get_db)):
    rows = forward_income_rows(db)
    return csv_response(
        "forward_natural_income.csv",
        ["person", "account", "wrapper", "security", "quantity", "value", "yield", "forward_income", "source"],
        [
            [
                row["person"].name,
                row["account"].account_name,
                row["account"].wrapper_type,
                row["security"].ticker or row["security"].name,
                row["quantity"],
                row["value"],
                row["assumed_yield"] or "",
                row["forward_income"],
                row["source"],
            ]
            for row in rows
        ],
    )


@app.get("/reports/unmatched-securities.csv")
def report_unmatched(db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Transaction).where(
            Transaction.security_id.is_(None),
            Transaction.transaction_type.in_(SECURITY_MATCH_REQUIRED_TYPES),
        )
    ).all()
    return csv_response(
        "unmatched_securities.csv",
        ["transaction_id", "date", "description", "type", "amount"],
        [[row.id, row.transaction_date, row.description, row.transaction_type, row.net_amount] for row in rows],
    )


@app.get("/reports/import-warnings.csv")
def report_import_warnings(db: Session = Depends(get_db)):
    rows = db.scalars(
        select(ImportRow)
        .join(ImportRow.job)
        .where(ImportRow.warnings.is_not(None), ImportJob.status != "ROLLED_BACK")
        .order_by(ImportRow.id.desc())
    ).all()
    output_rows = []
    for row in rows:
        normalized = json.loads(row.normalized_json or "{}")
        output_rows.append(
            [
                row.import_job_id,
                row.job.original_filename,
                row.job.account.account_name if row.job.account else "",
                row.row_number,
                normalized.get("description") or normalized.get("name") or "",
                row.warnings,
                row.committed,
            ]
        )
    return csv_response(
        "import_warnings.csv",
        ["import_id", "file", "account", "line", "description", "warnings", "committed"],
        output_rows,
    )


@app.get("/reports/cash-activity.csv")
def report_cash_activity(db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Transaction)
        .where(
            Transaction.transaction_type.in_(
                [
                    "ACCOUNT_CHARGE",
                    "CASH_WITHDRAWAL",
                    "GROSS_INTEREST",
                    "OPENING_BALANCE",
                ]
            )
        )
        .order_by(Transaction.transaction_date, Transaction.id)
    ).all()
    return csv_response(
        "cash_activity.csv",
        [
            "date",
            "person",
            "account",
            "wrapper",
            "source_account_code",
            "type",
            "description",
            "amount",
            "fees",
        ],
        [
            [
                row.transaction_date,
                row.account.owner.name,
                row.account.account_name,
                row.account.wrapper_type,
                extract_account_code(row.description) or "",
                row.transaction_type,
                row.description,
                row.net_amount,
                row.fees,
            ]
            for row in rows
        ],
    )


@app.get("/reports/dividend-reconciliation.csv")
def report_reconciliation(db: Session = Depends(get_db)):
    rows = reconciliation_rows(db)
    return csv_response(
        "dividend_reconciliation.csv",
        ["receipt_id", "security", "payment_date", "actual", "expected", "difference", "status"],
        [
            [
                row["receipt"].id,
                row["receipt"].security.ticker if row["receipt"].security else "",
                row["receipt"].transaction_date,
                row["receipt"].net_amount,
                row["expected"] if row["expected"] is not None else "",
                row["difference"] if row["difference"] is not None else "",
                row["status"],
            ]
            for row in rows
        ],
    )
