from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import UPLOAD_DIR
from app.importers import (
    AIC_DIVIDEND_HISTORY,
    AIC_PORTFOLIO_INCOME,
    AJ_BELL_CASH_STATEMENT,
    AJ_BELL_HOLDINGS,
    AJ_BELL_TRANSACTIONS,
    detect_file_type,
    read_csv,
    safe_upload_name,
    stage_rows,
)
from app.models import (
    DividendEvent,
    AicPortfolioIncomeSnapshot,
    HoldingSnapshot,
    ImportJob,
    ImportRow,
    Security,
    SecurityIncomeAssumption,
    Transaction,
)
from app.security_matching import create_or_match_security, match_security


class ImportErrorDetail(ValueError):
    pass


def create_import_job(
    db: Session, filename: str, content: bytes, account_id: int | None
) -> ImportJob:
    if not content:
        raise ImportErrorDetail("The uploaded file is empty")
    digest = hashlib.sha256(content).hexdigest()
    try:
        headers, _ = read_csv(content)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ImportErrorDetail(str(exc)) from exc
    file_type = detect_file_type(headers)
    if file_type in {
        AJ_BELL_CASH_STATEMENT,
        AJ_BELL_HOLDINGS,
        AJ_BELL_TRANSACTIONS,
        AIC_PORTFOLIO_INCOME,
    } and not account_id:
        raise ImportErrorDetail("Select an account for this portfolio import")
    if file_type == AJ_BELL_CASH_STATEMENT and not db.scalar(
        select(HoldingSnapshot.id).where(HoldingSnapshot.account_id == account_id).limit(1)
    ):
        raise ImportErrorDetail(
            "Import and commit this account's AJ Bell portfolio CSV before its cash statement"
        )
    previous = db.scalar(
        select(ImportJob)
        .where(
            ImportJob.file_hash == digest,
            ImportJob.account_id == account_id,
            ImportJob.status == "COMMITTED",
        )
        .order_by(ImportJob.id.desc())
    )
    stored_path = UPLOAD_DIR / safe_upload_name(filename, digest)
    stored_path.write_bytes(content)
    staged, error_count, warning_count = stage_rows(content, file_type, account_id)
    job = ImportJob(
        original_filename=filename,
        stored_path=str(stored_path),
        file_hash=digest,
        detected_file_type=file_type,
        status="DUPLICATE" if previous else "STAGED",
        account_id=account_id,
        row_count=len(staged),
        warning_count=warning_count,
        error_count=error_count,
        duplicate_of_id=previous.id if previous else None,
    )
    db.add(job)
    db.flush()
    for data in staged:
        db.add(ImportRow(import_job_id=job.id, **data))
    db.flush()
    job.duplicate_count = count_existing_rows(db, job)
    db.commit()
    return job


def count_existing_rows(db: Session, job: ImportJob) -> int:
    hashes = [row.row_hash for row in job.rows if row.row_hash]
    if not hashes:
        return 0
    model = {
        AJ_BELL_HOLDINGS: HoldingSnapshot,
        AJ_BELL_TRANSACTIONS: Transaction,
        AJ_BELL_CASH_STATEMENT: Transaction,
        AIC_PORTFOLIO_INCOME: AicPortfolioIncomeSnapshot,
    }.get(job.detected_file_type)
    if model:
        return int(
            db.scalar(
                select(func.count()).select_from(model).where(model.source_row_hash.in_(hashes))
            )
            or 0
        )
    return 0


def commit_import(db: Session, job: ImportJob) -> dict[str, int]:
    if job.status == "COMMITTED":
        return {"committed": 0, "duplicates": job.row_count, "errors": 0}
    if job.status == "ROLLED_BACK":
        raise ImportErrorDetail("A rolled-back import cannot be committed")
    committed = duplicates = errors = 0
    for row in job.rows:
        if row.validation_errors:
            errors += 1
            continue
        data = json.loads(row.normalized_json or "{}")
        try:
            if job.detected_file_type == AJ_BELL_HOLDINGS:
                duplicate = db.scalar(
                    select(HoldingSnapshot.id).where(
                        HoldingSnapshot.source_row_hash == row.row_hash
                    )
                )
                if duplicate:
                    duplicates += 1
                    continue
                security = create_or_match_security(db, data)
                db.add(
                    HoldingSnapshot(
                        account_id=job.account_id,
                        security_id=security.id,
                        snapshot_date=date.fromisoformat(data["snapshot_date"]),
                        quantity=Decimal(data["quantity"]),
                        market_price=Decimal(data["market_price"]) if data.get("market_price") else None,
                        market_value=Decimal(data["market_value"]),
                        cost=Decimal(data["cost"]) if data.get("cost") else None,
                        currency=data["currency"],
                        source_import_id=job.id,
                        source_row_hash=row.row_hash,
                    )
                )
            elif job.detected_file_type in {
                AJ_BELL_CASH_STATEMENT,
                AJ_BELL_TRANSACTIONS,
            }:
                duplicate = db.scalar(
                    select(Transaction.id).where(Transaction.source_row_hash == row.row_hash)
                )
                if duplicate:
                    duplicates += 1
                    continue
                security = match_security(
                    db,
                    isin=data.get("isin"),
                    sedol=data.get("sedol"),
                    ticker=data.get("ticker"),
                    name=data.get("name") or data.get("description"),
                )
                if (
                    job.detected_file_type == AJ_BELL_CASH_STATEMENT
                    and data["transaction_type"] == "DIVIDEND"
                    and not security
                ):
                    row.warnings = "Dividend security was not matched to the imported portfolio"
                    job.warning_count += 1
                db.add(
                    Transaction(
                        account_id=job.account_id,
                        security_id=security.id if security else None,
                        transaction_date=date.fromisoformat(data["transaction_date"]),
                        settlement_date=(
                            date.fromisoformat(data["settlement_date"])
                            if data.get("settlement_date")
                            else None
                        ),
                        transaction_type=data["transaction_type"],
                        description=data["description"],
                        quantity=Decimal(data["quantity"]) if data.get("quantity") else None,
                        price=Decimal(data["price"]) if data.get("price") else None,
                        gross_amount=(
                            Decimal(data["gross_amount"]) if data.get("gross_amount") else None
                        ),
                        fees=Decimal(data["fees"]),
                        tax=Decimal(data["tax"]),
                        net_amount=Decimal(data["net_amount"]),
                        currency=data["currency"],
                        source_import_id=job.id,
                        source_row_hash=row.row_hash,
                    )
                )
            elif job.detected_file_type == AIC_DIVIDEND_HISTORY:
                security = match_security(
                    db, isin=data.get("isin"), ticker=data.get("ticker"), name=data.get("name")
                )
                if not security:
                    row.warnings = "No matching security; event not committed"
                    errors += 1
                    continue
                exists = db.scalar(
                    select(DividendEvent.id).where(
                        DividendEvent.security_id == security.id,
                        DividendEvent.payment_date == date.fromisoformat(data["payment_date"]),
                        DividendEvent.dividend_amount_per_share
                        == Decimal(data["dividend_amount_per_share"]),
                        DividendEvent.source == data["source"],
                    )
                )
                if exists:
                    duplicates += 1
                    continue
                db.add(
                    DividendEvent(
                        security_id=security.id,
                        ex_dividend_date=(
                            date.fromisoformat(data["ex_dividend_date"])
                            if data.get("ex_dividend_date")
                            else None
                        ),
                        payment_date=date.fromisoformat(data["payment_date"]),
                        dividend_amount_per_share=Decimal(
                            data["dividend_amount_per_share"]
                        ),
                        currency=data["currency"],
                        dividend_type=data["dividend_type"],
                        source=data["source"],
                        source_url=data.get("source_url"),
                        source_import_id=job.id,
                    )
                )
            elif job.detected_file_type == AIC_PORTFOLIO_INCOME:
                duplicate = db.scalar(
                    select(AicPortfolioIncomeSnapshot.id).where(
                        AicPortfolioIncomeSnapshot.source_row_hash == row.row_hash
                    )
                )
                if duplicate:
                    duplicates += 1
                    continue
                security = create_or_match_security(db, data, source="AIC")
                if data.get("aic_sector") and not security.sector:
                    security.sector = data["aic_sector"]
                db.add(
                    AicPortfolioIncomeSnapshot(
                        account_id=job.account_id,
                        security_id=security.id,
                        snapshot_date=job.uploaded_at.date(),
                        income_received=Decimal(data["income_received"]),
                        shares_held=Decimal(data["shares_held"]),
                        trailing_yield=Decimal(data["trailing_yield"]) if data.get("trailing_yield") else None,
                        dividend_frequency=data.get("dividend_frequency"),
                        aic_sector=data.get("aic_sector"),
                        source_import_id=job.id,
                        source_row_hash=row.row_hash,
                    )
                )
            else:
                raise ImportErrorDetail("Unknown imports cannot be committed")
            row.committed = True
            committed += 1
        except (ValueError, KeyError) as exc:
            row.validation_errors = str(exc)
            errors += 1
    job.status = "COMMITTED"
    job.committed_at = datetime.now(timezone.utc)
    job.duplicate_count = duplicates
    job.error_count = errors
    db.commit()
    return {"committed": committed, "duplicates": duplicates, "errors": errors}


def rollback_import(db: Session, job: ImportJob) -> None:
    if job.status != "COMMITTED":
        raise ImportErrorDetail("Only a committed import can be rolled back")
    db.execute(delete(Transaction).where(Transaction.source_import_id == job.id))
    db.execute(delete(HoldingSnapshot).where(HoldingSnapshot.source_import_id == job.id))
    db.execute(delete(DividendEvent).where(DividendEvent.source_import_id == job.id))
    db.execute(delete(AicPortfolioIncomeSnapshot).where(AicPortfolioIncomeSnapshot.source_import_id == job.id))
    for row in job.rows:
        row.committed = False
    job.status = "ROLLED_BACK"
    job.rolled_back_at = datetime.now(timezone.utc)
    db.commit()


def uk_tax_year(value: date) -> str:
    start = value.year if (value.month, value.day) >= (4, 6) else value.year - 1
    return f"{start}/{str(start + 1)[-2:]}"


def current_holdings(db: Session) -> list[HoldingSnapshot]:
    snapshots = db.scalars(
        select(HoldingSnapshot)
        .join(HoldingSnapshot.account)
        .join(HoldingSnapshot.security)
        .order_by(HoldingSnapshot.snapshot_date.desc(), HoldingSnapshot.id.desc())
    ).all()
    latest: dict[tuple[int, int], HoldingSnapshot] = {}
    for snapshot in snapshots:
        latest.setdefault((snapshot.account_id, snapshot.security_id), snapshot)
    return list(latest.values())


FALLBACK_YIELDS = {
    "Investment Trust": Decimal("0.04"),
    "ETF": Decimal("0.03"),
    "Fund": Decimal("0.03"),
    "Equity": Decimal("0.035"),
    "Cash": Decimal("0"),
    "Other": Decimal("0"),
}


def forward_income_rows(db: Session) -> list[dict]:
    result: list[dict] = []
    for holding in current_holdings(db):
        security = holding.security
        assumption = db.scalar(
            select(SecurityIncomeAssumption)
            .where(
                SecurityIncomeAssumption.security_id == security.id,
                SecurityIncomeAssumption.active.is_(True),
            )
            .order_by(
                SecurityIncomeAssumption.assumption_date.desc(),
                SecurityIncomeAssumption.id.desc(),
            )
        )
        annual_dps: Decimal | None = None
        assumed_yield: Decimal | None = None
        if assumption and assumption.forward_annual_dividend_per_share is not None:
            annual_dps = Decimal(assumption.forward_annual_dividend_per_share)
            income = Decimal(holding.quantity) * annual_dps
            source = f"Manual: {assumption.source}"
            assumed_yield = income / Decimal(holding.market_value) if holding.market_value else None
        elif assumption and assumption.forward_yield is not None:
            assumed_yield = Decimal(assumption.forward_yield)
            income = Decimal(holding.market_value) * assumed_yield
            source = f"Manual yield: {assumption.source}"
        else:
            aic_snapshot = db.scalar(
                select(AicPortfolioIncomeSnapshot)
                .where(
                    AicPortfolioIncomeSnapshot.account_id == holding.account_id,
                    AicPortfolioIncomeSnapshot.security_id == security.id,
                )
                .order_by(AicPortfolioIncomeSnapshot.snapshot_date.desc(), AicPortfolioIncomeSnapshot.id.desc())
            )
            if aic_snapshot and aic_snapshot.shares_held:
                annual_dps = Decimal(aic_snapshot.income_received) / Decimal(aic_snapshot.shares_held)
                income = Decimal(holding.quantity) * annual_dps
                source = "AIC portfolio (trailing 12 months)"
                assumed_yield = income / Decimal(holding.market_value) if holding.market_value else None
                result.append(
                    {
                        "holding": holding, "person": holding.account.owner, "account": holding.account,
                        "security": security, "value": Decimal(holding.market_value),
                        "quantity": Decimal(holding.quantity), "annual_dps": annual_dps,
                        "assumed_yield": assumed_yield, "forward_income": income.quantize(Decimal("0.01")),
                        "source": source,
                    }
                )
                continue
            actual_receipts = db.scalars(
                select(Transaction).where(
                    Transaction.account_id == holding.account_id,
                    Transaction.security_id == security.id,
                    Transaction.transaction_type == "DIVIDEND",
                    Transaction.transaction_date >= date.today() - timedelta(days=365),
                    Transaction.transaction_date <= date.today(),
                )
            ).all()
            payments_with_quantity = [
                item
                for item in actual_receipts
                if item.quantity is not None and Decimal(item.quantity) > 0
            ]
            if payments_with_quantity:
                annual_dps = sum(
                    (
                        Decimal(item.net_amount) / Decimal(item.quantity)
                        for item in payments_with_quantity
                    ),
                    Decimal("0"),
                )
                income = Decimal(holding.quantity) * annual_dps
                source = "AJ Bell actual receipts (trailing 12 months, includes specials)"
                assumed_yield = (
                    income / Decimal(holding.market_value) if holding.market_value else None
                )
                result.append(
                    {
                        "holding": holding,
                        "person": holding.account.owner,
                        "account": holding.account,
                        "security": security,
                        "value": Decimal(holding.market_value),
                        "quantity": Decimal(holding.quantity),
                        "annual_dps": annual_dps,
                        "assumed_yield": assumed_yield,
                        "forward_income": income.quantize(Decimal("0.01")),
                        "source": source,
                    }
                )
                continue
            cutoff = date.today() - timedelta(days=365)
            events = db.scalars(
                select(DividendEvent).where(
                    DividendEvent.security_id == security.id,
                    DividendEvent.payment_date >= cutoff,
                    DividendEvent.payment_date <= date.today(),
                )
            ).all()
            if events:
                annual_dps = sum(
                    (Decimal(event.dividend_amount_per_share) for event in events),
                    Decimal("0"),
                )
                income = Decimal(holding.quantity) * annual_dps
                source = "Dividend events (trailing 12 months)"
                assumed_yield = (
                    income / Decimal(holding.market_value) if holding.market_value else None
                )
            else:
                assumed_yield = FALLBACK_YIELDS.get(
                    security.asset_type, FALLBACK_YIELDS["Other"]
                )
                income = Decimal(holding.market_value) * assumed_yield
                source = f"{security.asset_type} fallback yield"
        result.append(
            {
                "holding": holding,
                "person": holding.account.owner,
                "account": holding.account,
                "security": security,
                "value": Decimal(holding.market_value),
                "quantity": Decimal(holding.quantity),
                "annual_dps": annual_dps,
                "assumed_yield": assumed_yield,
                "forward_income": income.quantize(Decimal("0.01")),
                "source": source,
            }
        )
    return result


def historic_income_rows(db: Session) -> list[dict]:
    transactions = db.scalars(
        select(Transaction)
        .where(Transaction.transaction_type.in_(["DIVIDEND", "INTEREST"]))
        .order_by(Transaction.transaction_date.desc())
    ).all()
    return [
        {
            "transaction": item,
            "calendar_year": item.transaction_date.year,
            "tax_year": uk_tax_year(item.transaction_date),
            "person": item.account.owner,
            "account": item.account,
            "security": item.security,
            "dividends": (
                Decimal(item.net_amount) if item.transaction_type == "DIVIDEND" else Decimal("0")
            ),
            "interest": (
                Decimal(item.net_amount) if item.transaction_type == "INTEREST" else Decimal("0")
            ),
            "total": Decimal(item.net_amount),
            "quantity": Decimal(item.quantity) if item.quantity is not None else None,
            "per_share": (
                Decimal(item.net_amount) / Decimal(item.quantity)
                if item.transaction_type == "DIVIDEND"
                and item.quantity is not None
                and Decimal(item.quantity) > 0
                else None
            ),
        }
        for item in transactions
    ]


def aggregate_income(rows: list[dict], keys: tuple[str, ...]) -> list[dict]:
    groups: dict[tuple, dict] = {}
    for row in rows:
        group_key = tuple(
            getattr(row[key], "name", None)
            or getattr(row[key], "account_name", None)
            or getattr(row[key], "wrapper_type", None)
            or row[key]
            for key in keys
        )
        target = groups.setdefault(
            group_key,
            {**{key: value for key, value in zip(keys, group_key)}, "dividends": Decimal("0"), "interest": Decimal("0"), "total": Decimal("0")},
        )
        for amount_key in ("dividends", "interest", "total"):
            target[amount_key] += row[amount_key]
    return sorted(groups.values(), key=lambda item: tuple(str(item[k]) for k in keys))


def reconciliation_rows(
    db: Session, date_tolerance_days: int = 10, amount_tolerance: Decimal = Decimal("0.02")
) -> list[dict]:
    receipts = db.scalars(
        select(Transaction).where(Transaction.transaction_type == "DIVIDEND")
    ).all()
    holdings = current_holdings(db)
    holdings_by_security_account = {
        (item.security_id, item.account_id): item for item in holdings
    }
    output = []
    for receipt in receipts:
        base = {
            "receipt": receipt,
            "event": None,
            "expected": None,
            "difference": None,
            "status": "No dividend event",
        }
        if not receipt.security_id:
            base["status"] = "Unmatched security"
            output.append(base)
            continue
        events = db.scalars(
            select(DividendEvent).where(
                DividendEvent.security_id == receipt.security_id,
                DividendEvent.payment_date
                >= receipt.transaction_date - timedelta(days=date_tolerance_days),
                DividendEvent.payment_date
                <= receipt.transaction_date + timedelta(days=date_tolerance_days),
            )
        ).all()
        if not events:
            output.append(base)
            continue
        event = min(
            events, key=lambda item: abs((item.payment_date - receipt.transaction_date).days)
        )
        base["event"] = event
        holding = holdings_by_security_account.get((receipt.security_id, receipt.account_id))
        if not holding:
            base["status"] = "No holding quantity"
            output.append(base)
            continue
        expected = Decimal(holding.quantity) * Decimal(event.dividend_amount_per_share)
        actual = Decimal(receipt.net_amount)
        difference = actual - expected
        base["expected"] = expected.quantize(Decimal("0.01"))
        base["difference"] = difference.quantize(Decimal("0.01"))
        if expected and abs(difference / expected) > amount_tolerance:
            base["status"] = "Amount mismatch"
        else:
            base["status"] = "Matched"
        output.append(base)
    return output
