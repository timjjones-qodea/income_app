from __future__ import annotations

import csv
import io
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db, init_db
from app.models import (
    Account,
    ImportJob,
    Person,
    Security,
    SecurityIncomeAssumption,
    Transaction,
)
from app.security_matching import save_manual_mapping
from app.services import (
    ImportErrorDetail,
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


def seed() -> None:
    with SessionLocal() as db:
        people = {}
        for name in ("Tim", "Wife"):
            person = db.scalar(select(Person).where(Person.name == name))
            if not person:
                person = Person(name=name, tax_residency="UK")
                db.add(person)
                db.flush()
            people[name] = person
        for owner, wrapper in (
            ("Tim", "ISA"),
            ("Tim", "SIPP"),
            ("Wife", "ISA"),
            ("Wife", "SIPP"),
        ):
            account_name = f"{owner} {wrapper}"
            if not db.scalar(select(Account).where(Account.account_name == account_name)):
                db.add(
                    Account(
                        provider="AJ Bell",
                        account_name=account_name,
                        owner_person_id=people[owner].id,
                        wrapper_type=wrapper,
                        currency="GBP",
                    )
                )
        db.commit()


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
        "unmatched": int(
            db.scalar(
                select(func.count()).select_from(Transaction).where(
                    Transaction.security_id.is_(None),
                    Transaction.transaction_type.in_(["DIVIDEND", "BUY", "SELL"]),
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


@app.get("/imports", response_class=HTMLResponse)
def imports_page(request: Request, db: Session = Depends(get_db)):
    jobs = db.scalars(select(ImportJob).order_by(ImportJob.id.desc())).all()
    accounts = db.scalars(select(Account).order_by(Account.account_name)).all()
    people = db.scalars(select(Person).order_by(Person.name)).all()
    return render(request, "imports.html", jobs=jobs, accounts=accounts, people=people)


@app.post("/accounts")
def create_account(
    account_name: str = Form(...),
    owner_person_id: int = Form(...),
    wrapper_type: str = Form(...),
    aic_portfolio_url: str = Form(""),
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


@app.get("/holdings", response_class=HTMLResponse)
def holdings_page(request: Request, db: Session = Depends(get_db)):
    rows = forward_income_rows(db)
    return render(
        request,
        "holdings.html",
        rows=rows,
        total_value=sum((row["value"] for row in rows), Decimal("0")),
        total_income=sum((row["forward_income"] for row in rows), Decimal("0")),
    )


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
    return render(request, "holdings.html", rows=forward_income_rows(db), title="Forward income")


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
        select(Transaction).where(Transaction.security_id.is_(None)).order_by(Transaction.id.desc())
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
    rows = db.scalars(select(Transaction).where(Transaction.security_id.is_(None))).all()
    return csv_response(
        "unmatched_securities.csv",
        ["transaction_id", "date", "description", "type", "amount"],
        [[row.id, row.transaction_date, row.description, row.transaction_type, row.net_amount] for row in rows],
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
