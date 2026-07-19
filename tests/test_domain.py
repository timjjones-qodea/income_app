from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models import (
    DividendEvent,
    HoldingSnapshot,
    ImportJob,
    Security,
    SecurityIncomeAssumption,
    Transaction,
)
from app.security_matching import match_security, save_manual_mapping
from app.services import (
    forward_income_rows,
    historic_income_rows,
    reconciliation_rows,
    uk_tax_year,
)


def add_job(db, account):
    job = ImportJob(
        original_filename="test.csv",
        stored_path="/tmp/test.csv",
        file_hash="a" * 64,
        detected_file_type="TEST",
        status="COMMITTED",
        account_id=account.id,
    )
    db.add(job)
    db.flush()
    return job


def add_security(db):
    security = Security(
        name="City of London Investment Trust",
        ticker="CTY",
        isin="GB0001990497",
        sedol="0199049",
        asset_type="Investment Trust",
    )
    db.add(security)
    db.flush()
    return security


def test_security_matching_by_isin_and_ticker(db):
    security = add_security(db)
    assert match_security(db, isin="gb0001990497").id == security.id
    assert match_security(db, ticker="cty").id == security.id


def test_manual_mapping_persists(db):
    security = add_security(db)
    save_manual_mapping(db, "CITY OF LONDON INV TRUST ORD 25P", security.id)
    db.commit()
    assert (
        match_security(db, name="CITY OF LONDON INV TRUST ORD 25P").id == security.id
    )


def test_uk_tax_year_boundaries():
    assert uk_tax_year(date(2026, 4, 5)) == "2025/26"
    assert uk_tax_year(date(2026, 4, 6)) == "2026/27"


def test_historic_income_by_year(db, account):
    job = add_job(db, account)
    security = add_security(db)
    for index, (payment_date, kind, amount) in enumerate(
        [
            (date(2024, 12, 1), "DIVIDEND", "100"),
            (date(2024, 12, 2), "INTEREST", "10"),
            (date(2024, 12, 3), "GROSS_INTEREST", "2"),
            (date(2025, 1, 2), "SELL", "999"),
        ]
    ):
        db.add(
            Transaction(
                account_id=account.id,
                security_id=security.id,
                transaction_date=payment_date,
                transaction_type=kind,
                description=kind,
                net_amount=Decimal(amount),
                source_import_id=job.id,
                source_row_hash=f"row-{index}",
            )
        )
    db.commit()
    rows = historic_income_rows(db)
    assert len(rows) == 3
    assert sum(row["total"] for row in rows if row["calendar_year"] == 2024) == 112


def add_holding(db, account, security, job, value="10000", quantity="1000"):
    holding = HoldingSnapshot(
        account_id=account.id,
        security_id=security.id,
        snapshot_date=date(2026, 7, 4),
        quantity=Decimal(quantity),
        market_price=Decimal("10"),
        market_value=Decimal(value),
        currency="GBP",
        source_import_id=job.id,
        source_row_hash=f"holding-{security.id}",
    )
    db.add(holding)
    db.flush()
    return holding


def test_forward_income_from_dividend_per_share(db, account):
    job = add_job(db, account)
    security = add_security(db)
    add_holding(db, account, security, job)
    db.add(
        SecurityIncomeAssumption(
            security_id=security.id,
            assumption_date=date.today(),
            forward_annual_dividend_per_share=Decimal("0.50"),
            source="Manual",
            active=True,
        )
    )
    db.commit()
    row = forward_income_rows(db)[0]
    assert row["forward_income"] == Decimal("500.00")
    assert row["source"].startswith("Manual")


def test_forward_income_from_yield_fallback(db, account):
    job = add_job(db, account)
    security = add_security(db)
    add_holding(db, account, security, job)
    db.commit()
    row = forward_income_rows(db)[0]
    assert row["assumed_yield"] == Decimal("0.04")
    assert row["forward_income"] == Decimal("400.00")


def test_reconciliation_flags_amount_mismatch(db, account):
    job = add_job(db, account)
    security = add_security(db)
    add_holding(db, account, security, job, quantity="1000")
    db.add(
        DividendEvent(
            security_id=security.id,
            payment_date=date(2026, 6, 30),
            dividend_amount_per_share=Decimal("0.10"),
            source="AIC",
        )
    )
    db.add(
        Transaction(
            account_id=account.id,
            security_id=security.id,
            transaction_date=date(2026, 7, 2),
            transaction_type="DIVIDEND",
            description="Dividend",
            net_amount=Decimal("80"),
            source_import_id=job.id,
            source_row_hash="receipt-1",
        )
    )
    db.commit()
    row = reconciliation_rows(db)[0]
    assert row["expected"] == Decimal("100.00")
    assert row["status"] == "Amount mismatch"
