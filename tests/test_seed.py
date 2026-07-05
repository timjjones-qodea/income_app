from sqlalchemy import select

from app.main import seed_reference_data
from app.models import Account, Person


def test_seed_migrates_wife_to_wendy_and_adds_gias(db):
    wife = Person(name="Wife", tax_residency="UK")
    tim = Person(name="Tim", tax_residency="UK")
    db.add_all([wife, tim])
    db.flush()
    wife_isa = Account(
        provider="AJ Bell",
        account_name="Wife ISA",
        owner_person_id=wife.id,
        wrapper_type="ISA",
        currency="GBP",
    )
    wife_sipp = Account(
        provider="AJ Bell",
        account_name="Wife SIPP",
        owner_person_id=wife.id,
        wrapper_type="SIPP",
        currency="GBP",
    )
    db.add_all([wife_isa, wife_sipp])
    db.commit()
    original_ids = {wife_isa.id, wife_sipp.id}

    seed_reference_data(db)
    seed_reference_data(db)

    people = {person.name for person in db.scalars(select(Person))}
    accounts = {
        account.account_name: account
        for account in db.scalars(select(Account).order_by(Account.account_name))
    }
    assert people == {"Tim", "Wendy"}
    assert set(accounts) == {
        "Tim GIA",
        "Tim ISA",
        "Tim SIPP",
        "Wendy GIA",
        "Wendy ISA",
        "Wendy SIPP",
    }
    assert {accounts["Wendy ISA"].id, accounts["Wendy SIPP"].id} == original_ids
    assert accounts["Wendy ISA"].owner.name == "Wendy"
    assert accounts["Tim GIA"].tax_treatment == "VCT dividends treated as tax-free"
    assert accounts["Wendy GIA"].tax_treatment == "Unwrapped taxable investment account"

