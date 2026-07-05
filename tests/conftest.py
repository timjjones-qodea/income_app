from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Account, Person


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        yield session
    Base.metadata.drop_all(engine)


@pytest.fixture()
def account(db):
    person = Person(name="Tim", tax_residency="UK")
    db.add(person)
    db.flush()
    item = Account(
        provider="AJ Bell",
        account_name="Tim ISA",
        owner_person_id=person.id,
        wrapper_type="ISA",
        currency="GBP",
    )
    db.add(item)
    db.commit()
    return item

