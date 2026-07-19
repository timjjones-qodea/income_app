from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


AJ_BELL_HOLDINGS = "AJ_BELL_HOLDINGS"
AJ_BELL_TRANSACTIONS = "AJ_BELL_TRANSACTIONS"
AJ_BELL_CASH_STATEMENT = "AJ_BELL_CASH_STATEMENT"
AIC_DIVIDEND_HISTORY = "AIC_DIVIDEND_HISTORY"
AIC_PORTFOLIO_INCOME = "AIC_PORTFOLIO_INCOME"
UNKNOWN = "UNKNOWN"


def clean_header(value: str) -> str:
    value = value.lstrip("\ufeff").strip().lower()
    value = value.replace("£", " gbp ").replace("%", " percent ")
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")


def read_csv(content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    text = content.decode("utf-8-sig")
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("The CSV has no header row")
    headers = [clean_header(item or "") for item in reader.fieldnames]
    rows: list[dict[str, str]] = []
    for raw in reader:
        normalized = {
            clean_header(key or ""): (value or "").strip()
            for key, value in raw.items()
            if key is not None
        }
        if any(normalized.values()):
            rows.append(normalized)
    return headers, rows


def detect_file_type(headers: list[str]) -> str:
    columns = set(headers)
    if {"company", "aic_sector", "income_received", "shares_held", "div_freq", "yield_percent"}.issubset(columns):
        return AIC_PORTFOLIO_INCOME
    if {"investment", "quantity"}.issubset(columns) and (
        "value_gbp" in columns or "value" in columns
    ):
        return AJ_BELL_HOLDINGS
    if {
        "date",
        "description",
        "receipt_gbp",
        "payment_gbp",
        "balance_gbp",
    }.issubset(columns):
        return AJ_BELL_CASH_STATEMENT
    if (
        {"payment_date", "dividend_per_share"}.issubset(columns)
        or {"pay_date", "dividend_amount_per_share"}.issubset(columns)
    ):
        return AIC_DIVIDEND_HISTORY
    date_columns = {"date", "transaction_date", "trade_date"}
    amount_columns = {"amount", "net_amount", "value", "credit", "debit"}
    if columns.intersection(date_columns) and columns.intersection(amount_columns):
        return AJ_BELL_TRANSACTIONS
    return UNKNOWN


def first(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = row.get(name, "").strip()
        if value:
            return value
    return ""


def parse_decimal(value: str, *, required: bool = False) -> Decimal | None:
    text = (value or "").strip()
    if not text:
        if required:
            raise ValueError("value is required")
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace(",", "").replace("£", "").replace("$", "").replace("€", "")
    text = text.replace("%", "").strip()
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"invalid number: {value}") from exc
    return -parsed if negative else parsed


DATE_FORMATS = (
    "%d-%b-%y",
    "%d-%b-%Y",
    "%d/%m/%Y",
    "%d/%m/%y",
    "%Y-%m-%d",
    "%d %b %Y",
)


def parse_date(value: str, *, required: bool = False):
    text = (value or "").strip()
    if not text:
        if required:
            raise ValueError("date is required")
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"invalid date: {value}")


def normalize_name(value: str) -> str:
    value = value.upper()
    value = re.sub(r"\(LSE:[^)]+\)", "", value)
    value = re.sub(
        r"^(DIVIDEND|PURCHASE|SALE|INCOME\s+PAYMENT|DISTRIBUTION)\s+[\d,]+(?:\.\d+)?\s+",
        "",
        value,
    )
    value = re.sub(r"\bJ\s+P\s+MORGAN\b", "JPMORGAN", value)
    value = re.sub(r"\bJ\s+PMORGAN\b", "JPMORGAN", value)
    value = re.sub(r"\bLAWDEBENTURE\b", "LAW DEBENTURE", value)
    value = re.sub(r"\bS\s*/\s*T\b", "SHORT TERM", value)
    value = re.sub(r"\bSTLG\b", "STERLING", value)
    value = re.sub(r"\bMNY\b", "MONEY", value)
    value = re.sub(r"\bMKTS\b", "MARKETS", value)
    value = re.sub(r"\bCORP\b", "CORPORATION", value)
    value = re.sub(r"\bINC\b", "INCOME", value)
    value = re.sub(r"\bGRWT\b", "GROWTH", value)
    value = re.sub(r"\bINV\b", "INVESTMENT", value)
    value = re.sub(r"\bVCT\s+([0-9]+)\b", r"VCT\1", value)
    value = re.sub(r"\bGBP\s*[\d.]*\b", "", value)
    value = re.sub(r"\bGBX\s*[\d.]*\b", "", value)
    value = re.sub(r"\b(PLC|LIMITED|LTD|ORDINARY|ORD|P|ACC|INVESTMENT|TRUST|FUND)\b", "", value)
    return re.sub(r"[^A-Z0-9]+", " ", value).strip()


def row_hash(kind: str, normalized: dict[str, Any], account_id: int | None) -> str:
    payload = json.dumps(
        {"kind": kind, "account_id": account_id, "row": normalized},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def extract_ticker(investment: str, explicit: str = "") -> str | None:
    if explicit.strip():
        return explicit.strip().upper()
    match = re.search(r"\bLSE:([A-Z0-9.]+)", investment.upper())
    return match.group(1) if match else None


def normalize_holding(row: dict[str, str]) -> tuple[dict[str, Any], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    name = first(row, "investment", "security", "name", "description")
    try:
        quantity = parse_decimal(first(row, "quantity", "units", "holding"), required=True)
    except ValueError as exc:
        quantity = None
        errors.append(str(exc))
    try:
        value = parse_decimal(first(row, "value_gbp", "value", "market_value"), required=True)
    except ValueError as exc:
        value = None
        errors.append(str(exc))
    try:
        snapshot_date = parse_date(first(row, "date", "valuation_date"), required=True)
    except ValueError as exc:
        snapshot_date = None
        errors.append(str(exc))
    if not name:
        errors.append("investment name is required")
    ticker = extract_ticker(name, first(row, "ticker", "epic"))
    is_cash = name.upper().startswith("CASH")
    if not ticker and not is_cash:
        warnings.append("No ticker supplied; security will need review")
    normalized = {
        "name": name,
        "normalized_name": normalize_name(name),
        "ticker": ticker,
        "isin": first(row, "isin").upper() or None,
        "sedol": first(row, "sedol").upper() or None,
        "quantity": str(quantity) if quantity is not None else None,
        "market_price": str(parse_decimal(first(row, "price", "market_price")) or "") or None,
        "market_value": str(value) if value is not None else None,
        "cost": str(parse_decimal(first(row, "cost_gbp", "cost")) or "") or None,
        "currency": first(row, "valuation_currency", "currency") or "GBP",
        "snapshot_date": snapshot_date.isoformat() if snapshot_date else None,
        "asset_type": "Cash" if is_cash else "Investment Trust",
        "exchange": "LSE" if "LSE:" in name.upper() else None,
    }
    return normalized, errors, warnings


TYPE_KEYWORDS = {
    "GROSS_INTEREST": ("gross interest",),
    "ACCOUNT_CHARGE": ("account charge",),
    "CASH_WITHDRAWAL": ("cash withdrawal",),
    "OPENING_BALANCE": ("balance b/f", "balance brought forward"),
    "DIVIDEND": ("dividend", "distribution", "income payment"),
    "INTEREST": ("interest",),
    "BUY": ("purchase", "bought", "buy"),
    "SELL": ("sale", "sold", "sell"),
    "FEE": ("fee", "charge", "commission"),
    "TAX": ("tax", "withholding"),
    "CASH_IN": ("contribution", "cash in", "deposit"),
    "CASH_OUT": ("withdrawal", "cash out"),
    "TRANSFER": ("transfer",),
}


def extract_account_code(description: str) -> str | None:
    match = re.search(r"\b([A-Z]{4}\d[A-Z0-9]{2})\b", description.upper())
    return match.group(1) if match else None


def classify_transaction(explicit_type: str, description: str) -> str:
    haystack = f"{explicit_type} {description}".lower()
    for transaction_type, words in TYPE_KEYWORDS.items():
        if any(word in haystack for word in words):
            return transaction_type
    return "OTHER"


def cash_statement_rule(description: str) -> tuple[str | None, str | None]:
    text = description.strip()
    if re.match(r"^balance\s+b/f\b", text, re.IGNORECASE):
        return "OPENING_BALANCE", None
    if re.match(r"^account\s+charge\b", text, re.IGNORECASE):
        return "ACCOUNT_CHARGE", extract_account_code(text)
    if re.match(r"^cash\s+withdrawal\b", text, re.IGNORECASE):
        return "CASH_WITHDRAWAL", None
    if re.match(r"^gross\s+interest\s+to\b", text, re.IGNORECASE):
        return "GROSS_INTEREST", None
    return None, None


def normalize_transaction(row: dict[str, str]) -> tuple[dict[str, Any], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    description = first(row, "description", "details", "investment", "narrative")
    try:
        transaction_date = parse_date(
            first(row, "transaction_date", "trade_date", "date"), required=True
        )
    except ValueError as exc:
        transaction_date = None
        errors.append(str(exc))
    amount_text = first(row, "net_amount", "amount", "value")
    if not amount_text:
        credit = parse_decimal(first(row, "credit")) or Decimal("0")
        debit = parse_decimal(first(row, "debit")) or Decimal("0")
        amount_text = str(credit - debit)
    try:
        net_amount = parse_decimal(amount_text, required=True)
    except ValueError as exc:
        net_amount = None
        errors.append(str(exc))
    ticker = extract_ticker(description, first(row, "ticker", "epic"))
    transaction_type = classify_transaction(first(row, "type", "transaction_type"), description)
    if transaction_type in {"DIVIDEND", "INTEREST"} and not ticker:
        warnings.append("Income row has no ticker; security match may need review")
    quantity, security_name = transaction_security_details(description, transaction_type)
    normalized = {
        "transaction_date": transaction_date.isoformat() if transaction_date else None,
        "settlement_date": (
            parse_date(first(row, "settlement_date", "settled")).isoformat()
            if first(row, "settlement_date", "settled")
            else None
        ),
        "transaction_type": transaction_type,
        "description": description,
        "name": security_name,
        "ticker": ticker,
        "isin": first(row, "isin").upper() or None,
        "sedol": first(row, "sedol").upper() or None,
        "quantity": str(parse_decimal(first(row, "quantity", "units")) or quantity or "") or None,
        "price": str(parse_decimal(first(row, "price")) or "") or None,
        "gross_amount": str(parse_decimal(first(row, "gross_amount", "gross")) or "") or None,
        "fees": str(parse_decimal(first(row, "fees", "commission")) or Decimal("0")),
        "tax": str(parse_decimal(first(row, "tax", "withholding_tax")) or Decimal("0")),
        "net_amount": str(net_amount) if net_amount is not None else None,
        "currency": first(row, "currency") or "GBP",
    }
    return normalized, errors, warnings


def clean_aj_bell_security_name(value: str) -> str:
    value = re.sub(
        r"\s+(?:P\s+)?(?:ORD\s+)?GBP\s*[\d.]+\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\s+(?:ORD|ACC)\s*$", "", value, flags=re.IGNORECASE)
    return value.strip()


def transaction_security_details(
    description: str, transaction_type: str
) -> tuple[Decimal | None, str | None]:
    if transaction_type not in {"DIVIDEND", "BUY", "SELL"}:
        return None, None
    match = re.match(
        r"^(?:Dividend|Purchase|Sale)\s+([\d,]+(?:\.\d+)?)\s+(.+)$",
        description.strip(),
        re.IGNORECASE,
    )
    if not match:
        return None, None
    quantity = parse_decimal(match.group(1))
    security_name = clean_aj_bell_security_name(match.group(2))
    return quantity, security_name or None


def cash_dividend_details(description: str) -> tuple[Decimal | None, str | None]:
    return transaction_security_details(description, "DIVIDEND")


def normalize_cash_statement(
    row: dict[str, str],
) -> tuple[dict[str, Any], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    description = first(row, "description")
    try:
        transaction_date = parse_date(first(row, "date"), required=True)
    except ValueError as exc:
        transaction_date = None
        errors.append(str(exc))

    try:
        receipt = parse_decimal(first(row, "receipt_gbp")) or Decimal("0")
        payment = parse_decimal(first(row, "payment_gbp")) or Decimal("0")
        net_amount = receipt + (payment if payment <= 0 else -payment)
    except ValueError as exc:
        receipt = payment = net_amount = Decimal("0")
        errors.append(str(exc))

    cash_rule_type, source_account_code = cash_statement_rule(description)
    transaction_type = cash_rule_type or classify_transaction("", description)
    quantity, security_name = cash_dividend_details(description)
    if transaction_type == "DIVIDEND" and not security_name:
        warnings.append("Could not extract the security name from this dividend")

    settlement_text = first(row, "settlement_date")
    settlement_date = None
    if settlement_text and settlement_text != "-":
        try:
            settlement_date = parse_date(settlement_text)
        except ValueError as exc:
            errors.append(str(exc))

    normalized = {
        "transaction_date": transaction_date.isoformat() if transaction_date else None,
        "settlement_date": settlement_date.isoformat() if settlement_date else None,
        "transaction_type": transaction_type,
        "description": description,
        "source_account_code": source_account_code,
        "name": security_name,
        "ticker": None,
        "isin": None,
        "sedol": None,
        "quantity": str(quantity) if quantity is not None else None,
        "price": None,
        "gross_amount": str(receipt) if receipt else None,
        "fees": str(-net_amount if transaction_type == "ACCOUNT_CHARGE" and net_amount < 0 else 0),
        "tax": "0",
        "net_amount": str(net_amount),
        "currency": "GBP",
    }
    return normalized, errors, warnings


def normalize_dividend(row: dict[str, str]) -> tuple[dict[str, Any], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        payment_date = parse_date(first(row, "payment_date", "pay_date"), required=True)
    except ValueError as exc:
        payment_date = None
        errors.append(str(exc))
    try:
        amount = parse_decimal(
            first(row, "dividend_per_share", "dividend_amount_per_share", "amount"), required=True
        )
    except ValueError as exc:
        amount = None
        errors.append(str(exc))
    ticker = extract_ticker("", first(row, "ticker", "epic"))
    isin = first(row, "isin").upper() or None
    if not ticker and not isin:
        errors.append("ticker or ISIN is required")
    normalized = {
        "ticker": ticker,
        "isin": isin,
        "name": first(row, "security", "company", "investment", "name"),
        "ex_dividend_date": (
            parse_date(first(row, "ex_dividend_date", "ex_date")).isoformat()
            if first(row, "ex_dividend_date", "ex_date")
            else None
        ),
        "payment_date": payment_date.isoformat() if payment_date else None,
        "dividend_amount_per_share": str(amount) if amount is not None else None,
        "currency": first(row, "currency") or "GBP",
        "dividend_type": first(row, "dividend_type", "type") or "Ordinary",
        "source": first(row, "source") or "AIC CSV",
        "source_url": first(row, "source_url", "url") or None,
    }
    return normalized, errors, warnings


def normalize_aic_portfolio(row: dict[str, str]) -> tuple[dict[str, Any], list[str], list[str]]:
    errors: list[str] = []
    name = first(row, "company")
    try:
        income = parse_decimal(first(row, "income_received"), required=True)
        shares = parse_decimal(first(row, "shares_held"), required=True)
        trailing_yield = parse_decimal(first(row, "yield_percent"))
    except ValueError as exc:
        income = shares = trailing_yield = None
        errors.append(str(exc))
    if not name:
        errors.append("company is required")
    return {
        "name": name,
        "normalized_name": normalize_name(name),
        "income_received": str(income) if income is not None else None,
        "shares_held": str(shares) if shares is not None else None,
        "trailing_yield": str(trailing_yield / 100) if trailing_yield is not None else None,
        "dividend_frequency": first(row, "div_freq") or None,
        "aic_sector": first(row, "aic_sector") or None,
        "currency": "GBP",
        "asset_type": "Investment Trust",
    }, errors, []


NORMALIZERS = {
    AJ_BELL_HOLDINGS: normalize_holding,
    AJ_BELL_TRANSACTIONS: normalize_transaction,
    AJ_BELL_CASH_STATEMENT: normalize_cash_statement,
    AIC_DIVIDEND_HISTORY: normalize_dividend,
    AIC_PORTFOLIO_INCOME: normalize_aic_portfolio,
}


def stage_rows(
    content: bytes, file_type: str, account_id: int | None
) -> tuple[list[dict[str, Any]], int, int]:
    _headers, raw_rows = read_csv(content)
    normalizer = NORMALIZERS.get(file_type)
    staged: list[dict[str, Any]] = []
    errors = warnings = 0
    for number, raw in enumerate(raw_rows, start=2):
        if normalizer:
            normalized, row_errors, row_warnings = normalizer(raw)
            digest = row_hash(file_type, normalized, account_id)
        else:
            normalized, row_errors, row_warnings, digest = {}, ["Unknown CSV format"], [], None
        errors += bool(row_errors)
        warnings += bool(row_warnings)
        staged.append(
            {
                "row_number": number,
                "raw_json": json.dumps(raw, sort_keys=True),
                "normalized_json": json.dumps(normalized, sort_keys=True),
                "row_hash": digest,
                "validation_errors": "; ".join(row_errors) or None,
                "warnings": "; ".join(row_warnings) or None,
            }
        )
    return staged, errors, warnings


def safe_upload_name(original: str, digest: str) -> str:
    suffix = Path(original).suffix.lower() or ".csv"
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "_", Path(original).stem)[:80]
    return f"{digest[:12]}-{stem}{suffix}"
