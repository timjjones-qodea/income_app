from __future__ import annotations

import json
from pathlib import Path
from decimal import Decimal

from app.importers import (
    AIC_PORTFOLIO_INCOME,
    AJ_BELL_CASH_STATEMENT,
    AJ_BELL_HOLDINGS,
    AJ_BELL_TRANSACTIONS,
    classify_transaction,
    detect_file_type,
    read_csv,
    stage_rows,
)
from app.models import AicPortfolioIncomeSnapshot
from app.services import commit_import, create_import_job


def test_supplied_aj_bell_holdings_file_is_parsed():
    content = Path("sample_data/portfolio-ABWD2VI-ISA.csv").read_bytes()
    headers, rows = read_csv(content)
    assert detect_file_type(headers) == AJ_BELL_HOLDINGS
    staged, errors, warnings = stage_rows(content, AJ_BELL_HOLDINGS, 1)
    assert len(rows) == len(staged) == 11
    assert errors == 0
    city = staged[0]["normalized_json"]
    assert '"ticker": "CTY"' in city
    assert '"quantity": "14570"' in city


def test_transaction_format_and_classification():
    content = b"Date,Type,Description,Amount\n15-Jan-2025,Income,Quarterly dividend,125.40\n"
    headers, _ = read_csv(content)
    assert detect_file_type(headers) == AJ_BELL_TRANSACTIONS
    staged, errors, _warnings = stage_rows(content, AJ_BELL_TRANSACTIONS, 1)
    assert errors == 0
    assert '"transaction_type": "DIVIDEND"' in staged[0]["normalized_json"]
    assert classify_transaction("", "monthly cash interest") == "INTEREST"
    assert classify_transaction("Sale", "proceeds") == "SELL"


def test_duplicate_import_does_not_duplicate_holdings(db, account, tmp_path, monkeypatch):
    import app.services as services

    monkeypatch.setattr(services, "UPLOAD_DIR", tmp_path)
    content = Path("sample_data/portfolio-ABWD2VI-ISA.csv").read_bytes()
    first = create_import_job(db, "portfolio.csv", content, account.id)
    assert commit_import(db, first)["committed"] == 11
    second = create_import_job(db, "portfolio.csv", content, account.id)
    assert second.status == "DUPLICATE"
    result = commit_import(db, second)
    assert result["committed"] == 0
    assert result["duplicates"] == 11


def test_aic_portfolio_income_export(db, account, tmp_path, monkeypatch):
    import app.services as services

    monkeypatch.setattr(services, "UPLOAD_DIR", tmp_path)
    content = (
        b"Company,AIC sector,Income received,Shares held,Div freq,Yield (%)\n"
        b"City of London Investment Trust,UK Equity Income,3183.55,14570,Quarterly,3.83681\n"
    )
    headers, _ = read_csv(content)
    assert detect_file_type(headers) == AIC_PORTFOLIO_INCOME
    job = create_import_job(db, "Tim NISA.csv", content, account.id)
    assert commit_import(db, job)["committed"] == 1
    snapshot = db.query(AicPortfolioIncomeSnapshot).one()
    assert snapshot.income_received == Decimal("3183.55")
    assert snapshot.shares_held == Decimal("14570")


def test_aj_bell_cash_statement_rules_are_normalised():
    content = (
        b"Date,Description,Reference,Settlement date,Receipt (GBP),Payment (GBP),Balance (GBP)\n"
        b"01/07/2025,BALANCE B/F *,-,-,,,1000.00\n"
        b"30/06/2026,Account charge for shares - Jun 2026 - ABWD2VD,-,-,,4.99,995.01\n"
        b"30/06/2026,Cash Withdrawal,-,-,,100.00,895.01\n"
        b"30/06/2026,Gross interest to 30/06/26,-,-,1.23,,896.24\n"
    )
    headers, _rows = read_csv(content)
    assert detect_file_type(headers) == AJ_BELL_CASH_STATEMENT
    staged, errors, warnings = stage_rows(content, AJ_BELL_CASH_STATEMENT, 1)
    assert errors == 0
    assert warnings == 0
    normalised = [json.loads(row["normalized_json"]) for row in staged]
    assert [row["transaction_type"] for row in normalised] == [
        "OPENING_BALANCE",
        "ACCOUNT_CHARGE",
        "CASH_WITHDRAWAL",
        "GROSS_INTEREST",
    ]
    assert normalised[1]["source_account_code"] == "ABWD2VD"
    assert normalised[1]["fees"] == "4.99"
    assert normalised[2]["net_amount"] == "-100.00"
    assert normalised[3]["net_amount"] == "1.23"
