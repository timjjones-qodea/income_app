from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Person(Base):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    tax_residency: Mapped[str | None] = mapped_column(String(80))
    notes: Mapped[str | None] = mapped_column(Text)
    accounts: Mapped[list["Account"]] = relationship(back_populates="owner")


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(120), default="AJ Bell")
    account_name: Mapped[str] = mapped_column(String(160), unique=True)
    account_type: Mapped[str] = mapped_column(String(80), default="Investment")
    owner_person_id: Mapped[int] = mapped_column(ForeignKey("people.id"))
    wrapper_type: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    tax_treatment: Mapped[str | None] = mapped_column(String(160))
    notes: Mapped[str | None] = mapped_column(Text)
    aic_portfolio_url: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[Person] = relationship(back_populates="accounts")


class Security(Base):
    __tablename__ = "securities"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(240))
    ticker: Mapped[str | None] = mapped_column(String(32), unique=True)
    isin: Mapped[str | None] = mapped_column(String(20), unique=True)
    sedol: Mapped[str | None] = mapped_column(String(16), unique=True)
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    exchange: Mapped[str | None] = mapped_column(String(40))
    asset_type: Mapped[str] = mapped_column(String(60), default="Other")
    sector: Mapped[str | None] = mapped_column(String(120))
    aic_url: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class SecurityAlias(Base):
    __tablename__ = "security_aliases"
    __table_args__ = (UniqueConstraint("source", "external_identifier"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40), default="AJ Bell")
    external_identifier: Mapped[str] = mapped_column(String(240))
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    security: Mapped[Security] = relationship()


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(Text)
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    detected_file_type: Mapped[str] = mapped_column(String(60), default="UNKNOWN")
    status: Mapped[str] = mapped_column(String(30), default="STAGED")
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("import_jobs.id"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    account: Mapped[Account | None] = relationship(foreign_keys=[account_id])
    rows: Mapped[list["ImportRow"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="ImportRow.row_number"
    )


class ImportRow(Base):
    __tablename__ = "import_rows"
    __table_args__ = (UniqueConstraint("import_job_id", "row_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    import_job_id: Mapped[int] = mapped_column(ForeignKey("import_jobs.id", ondelete="CASCADE"))
    row_number: Mapped[int] = mapped_column(Integer)
    raw_json: Mapped[str] = mapped_column(Text)
    normalized_json: Mapped[str | None] = mapped_column(Text)
    row_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    validation_errors: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[str | None] = mapped_column(Text)
    committed: Mapped[bool] = mapped_column(Boolean, default=False)
    job: Mapped[ImportJob] = relationship(back_populates="rows")


class HoldingSnapshot(Base):
    __tablename__ = "holding_snapshots"
    __table_args__ = (
        UniqueConstraint("account_id", "security_id", "snapshot_date", "source_row_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"))
    snapshot_date: Mapped[date] = mapped_column(Date)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    market_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    market_value: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    source_import_id: Mapped[int] = mapped_column(ForeignKey("import_jobs.id"))
    source_row_hash: Mapped[str] = mapped_column(String(64), unique=True)
    account: Mapped[Account] = relationship()
    security: Mapped[Security] = relationship()


class AicPortfolioIncomeSnapshot(Base):
    __tablename__ = "aic_portfolio_income_snapshots"
    __table_args__ = (UniqueConstraint("source_row_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"))
    snapshot_date: Mapped[date] = mapped_column(Date)
    income_received: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    shares_held: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    trailing_yield: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    dividend_frequency: Mapped[str | None] = mapped_column(String(60))
    aic_sector: Mapped[str | None] = mapped_column(String(120))
    source_import_id: Mapped[int] = mapped_column(ForeignKey("import_jobs.id"))
    source_row_hash: Mapped[str] = mapped_column(String(64), unique=True)
    account: Mapped[Account] = relationship()
    security: Mapped[Security] = relationship()


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("source_row_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    security_id: Mapped[int | None] = mapped_column(ForeignKey("securities.id"))
    transaction_date: Mapped[date] = mapped_column(Date)
    settlement_date: Mapped[date | None] = mapped_column(Date)
    transaction_type: Mapped[str] = mapped_column(String(30))
    description: Mapped[str] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    gross_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    fees: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=0)
    tax: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=0)
    net_amount: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    source_import_id: Mapped[int] = mapped_column(ForeignKey("import_jobs.id"))
    source_row_hash: Mapped[str] = mapped_column(String(64), unique=True)
    account: Mapped[Account] = relationship()
    security: Mapped[Security | None] = relationship()


class DividendEvent(Base):
    __tablename__ = "dividend_events"
    __table_args__ = (
        UniqueConstraint("security_id", "payment_date", "dividend_amount_per_share", "source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"))
    ex_dividend_date: Mapped[date | None] = mapped_column(Date)
    payment_date: Mapped[date] = mapped_column(Date)
    dividend_amount_per_share: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    dividend_type: Mapped[str] = mapped_column(String(40), default="Ordinary")
    source: Mapped[str] = mapped_column(String(80), default="AIC CSV")
    source_url: Mapped[str | None] = mapped_column(Text)
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    source_import_id: Mapped[int | None] = mapped_column(ForeignKey("import_jobs.id"))
    security: Mapped[Security] = relationship()


class SecurityIncomeAssumption(Base):
    __tablename__ = "security_income_assumptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"))
    assumption_date: Mapped[date] = mapped_column(Date, default=date.today)
    forward_annual_dividend_per_share: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    forward_yield: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    dividend_growth_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    source: Mapped[str] = mapped_column(String(80), default="Manual")
    confidence: Mapped[str | None] = mapped_column(String(30))
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    security: Mapped[Security] = relationship()


class PlanningScenario(Base):
    __tablename__ = "planning_scenarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    tax_year: Mapped[str] = mapped_column(String(9), default="2026/27")
    tim_pension_withdrawal: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=180000)
    tim_isa_value: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=650000)
    wendy_isa_value: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=650000)
    isa_yield_percent: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=3.5)
    wendy_uk_property_profit: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=34000)
    wendy_french_property_gross: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=14100)
    wendy_french_tax_paid: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=3600)
    wendy_sole_trade_profit: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=6500)
    household_expenditure: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=75000)
    tim_expense_share_percent: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=50)
    tim_safety_margin: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=0)
    wendy_safety_margin: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=0)
    wendy_pcls: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=50000)
    isa_subscriptions: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=40000)
    projection_start_year: Mapped[int] = mapped_column(Integer, default=2027)
    projection_years: Mapped[int] = mapped_column(Integer, default=20)
    investment_growth_percent: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=5)
    inflation_percent: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=2.5)
    high_spend_years: Mapped[int] = mapped_column(Integer, default=10)
    later_household_expenditure: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=45000)
    tim_sipp_crystallised: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=1325000)
    tim_sipp_uncrystallised: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=1402000)
    wendy_sipp_crystallised: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=0)
    wendy_sipp_uncrystallised: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=650000)
    wendy_annual_crystallisation: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=200000)
    wendy_lump_sum_allowance: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=268275)
    tim_state_pension_start: Mapped[date] = mapped_column(Date, default=date(2033, 11, 13))
    wendy_state_pension_start: Mapped[date] = mapped_column(Date, default=date(2039, 5, 23))
    state_pension_annual: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=12547.60)
    state_pension_growth_percent: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=2.5)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


def default_planning_scenario(name: str = "New scenario") -> PlanningScenario:
    return PlanningScenario(
        name=name,
        tax_year="2026/27",
        tim_pension_withdrawal=Decimal("180000"),
        tim_isa_value=Decimal("650000"),
        wendy_isa_value=Decimal("650000"),
        isa_yield_percent=Decimal("3.5"),
        wendy_uk_property_profit=Decimal("34000"),
        wendy_french_property_gross=Decimal("14100"),
        wendy_french_tax_paid=Decimal("3600"),
        wendy_sole_trade_profit=Decimal("6500"),
        household_expenditure=Decimal("75000"),
        tim_expense_share_percent=Decimal("50"),
        tim_safety_margin=Decimal("0"),
        wendy_safety_margin=Decimal("0"),
        wendy_pcls=Decimal("50000"),
        isa_subscriptions=Decimal("40000"),
        projection_start_year=2027,
        projection_years=20,
        investment_growth_percent=Decimal("5"),
        inflation_percent=Decimal("2.5"),
        high_spend_years=10,
        later_household_expenditure=Decimal("45000"),
        tim_sipp_crystallised=Decimal("1325000"),
        tim_sipp_uncrystallised=Decimal("1402000"),
        wendy_sipp_crystallised=Decimal("0"),
        wendy_sipp_uncrystallised=Decimal("650000"),
        wendy_annual_crystallisation=Decimal("200000"),
        wendy_lump_sum_allowance=Decimal("268275"),
        tim_state_pension_start=date(2033, 11, 13),
        wendy_state_pension_start=date(2039, 5, 23),
        state_pension_annual=Decimal("12547.60"),
        state_pension_growth_percent=Decimal("2.5"),
    )
