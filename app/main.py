from __future__ import annotations

import csv
import io
import json
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
        security = match_security(db, name=transaction.description)
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
    calendar_year: int | None = None,
    tax_year: str | None = None,
    db: Session = Depends(get_db),
):
    rows = historic_income_rows(db)
    if person:
        rows = [row for row in rows if row["person"].name == person]
    if account:
        rows = [row for row in rows if row["account"].account_name == account]
    if wrapper:
        rows = [row for row in rows if row["account"].wrapper_type == wrapper]
    if security:
        rows = [row for row in rows if row["security"] and row["security"].ticker == security]
    if calendar_year:
        rows = [row for row in rows if row["calendar_year"] == calendar_year]
    if tax_year:
        rows = [row for row in rows if row["tax_year"] == tax_year]
    annual = aggregate_income(rows, ("calendar_year", "person", "account"))
    return render(request, "income.html", rows=rows, annual=annual)


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
