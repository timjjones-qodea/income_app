from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import DATABASE_URL, ensure_dirs


class Base(DeclarativeBase):
    pass


ensure_dirs()
engine_options: dict = {"future": True}
if DATABASE_URL.startswith("sqlite"):
    engine_options["connect_args"] = {"check_same_thread": False}
    if DATABASE_URL in {"sqlite://", "sqlite:///:memory:"}:
        engine_options["poolclass"] = StaticPool
engine = create_engine(DATABASE_URL, **engine_options)


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    if DATABASE_URL.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(engine)
    if DATABASE_URL.startswith("sqlite"):
        columns = {item["name"] for item in inspect(engine).get_columns("accounts")}
        if "aic_portfolio_url" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE accounts ADD COLUMN aic_portfolio_url TEXT"))
        if "tax_treatment" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE accounts ADD COLUMN tax_treatment VARCHAR(160)"))
        if "aj_bell_account_code" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE accounts ADD COLUMN aj_bell_account_code VARCHAR(16)"))
                connection.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS ix_accounts_aj_bell_account_code "
                        "ON accounts (aj_bell_account_code)"
                    )
                )
        planning_columns = {item["name"] for item in inspect(engine).get_columns("planning_scenarios")}
        planning_additions = {
            "projection_start_year": "INTEGER NOT NULL DEFAULT 2027",
            "projection_years": "INTEGER NOT NULL DEFAULT 20",
            "investment_growth_percent": "NUMERIC(8,4) NOT NULL DEFAULT 5",
            "inflation_percent": "NUMERIC(8,4) NOT NULL DEFAULT 2.5",
            "high_spend_years": "INTEGER NOT NULL DEFAULT 10",
            "later_household_expenditure": "NUMERIC(20,2) NOT NULL DEFAULT 45000",
            "tim_sipp_crystallised": "NUMERIC(20,2) NOT NULL DEFAULT 1325000",
            "tim_sipp_uncrystallised": "NUMERIC(20,2) NOT NULL DEFAULT 1402000",
            "wendy_sipp_crystallised": "NUMERIC(20,2) NOT NULL DEFAULT 0",
            "wendy_sipp_uncrystallised": "NUMERIC(20,2) NOT NULL DEFAULT 650000",
            "wendy_annual_crystallisation": "NUMERIC(20,2) NOT NULL DEFAULT 200000",
            "wendy_lump_sum_allowance": "NUMERIC(20,2) NOT NULL DEFAULT 268275",
            "tim_state_pension_start": "DATE NOT NULL DEFAULT '2033-11-13'",
            "wendy_state_pension_start": "DATE NOT NULL DEFAULT '2039-05-23'",
            "state_pension_annual": "NUMERIC(20,2) NOT NULL DEFAULT 12547.60",
            "state_pension_growth_percent": "NUMERIC(8,4) NOT NULL DEFAULT 2.5",
            "isa_capital_growth_percent": "NUMERIC(8,4) NOT NULL DEFAULT 1.5",
            "isa_allowance_per_person": "NUMERIC(20,2) NOT NULL DEFAULT 20000",
            "isa_contribution_per_person": "NUMERIC(20,2) NOT NULL DEFAULT 20000",
            "tim_withdrawal_start": "DATE NOT NULL DEFAULT '2027-04-06'",
            "wendy_first_crystallisation": "DATE NOT NULL DEFAULT '2027-05-23'",
        }
        with engine.begin() as connection:
            for column, definition in planning_additions.items():
                if column not in planning_columns:
                    connection.execute(
                        text(f"ALTER TABLE planning_scenarios ADD COLUMN {column} {definition}")
                    )
