from decimal import Decimal
from datetime import date

from app.models import default_planning_scenario
from app.planning import (
    ENGLAND_TAX_RULES,
    england_non_savings_income_tax,
    evaluate_scenario,
    gift_capacity,
    project_scenario,
)


def test_tim_180k_pension_baseline_matches_published_bands():
    result = england_non_savings_income_tax(
        Decimal("180000"), ENGLAND_TAX_RULES["2026/27"]
    )
    assert result.personal_allowance == Decimal("0.00")
    assert result.income_tax_due == Decimal("67203.00")
    assert Decimal("180000") - result.income_tax_due == Decimal("112797.00")


def test_gross_pension_contribution_restores_personal_allowance():
    result = england_non_savings_income_tax(
        Decimal("120000"),
        ENGLAND_TAX_RULES["2026/27"],
        gross_pension_contributions=Decimal("20000"),
    )
    assert result.adjusted_net_income == Decimal("100000.00")
    assert result.personal_allowance == Decimal("12570.00")
    assert result.income_tax_due == Decimal("31432.00")


def test_wendy_foreign_income_is_gross_and_credit_is_separate():
    result = england_non_savings_income_tax(
        Decimal("54600"),
        ENGLAND_TAX_RULES["2025/26"],
        foreign_income=Decimal("14100"),
        foreign_tax_paid=Decimal("3600"),
    )
    assert result.income_tax_before_credit == Decimal("9272.00")
    assert result.foreign_tax_credit == Decimal("3600.00")
    assert result.income_tax_due == Decimal("5672.00")


def test_gift_capacity_never_uses_capital_or_goes_negative():
    result = gift_capacity(Decimal("68078"), Decimal("37500"), Decimal("10000"))
    assert result.capacity == Decimal("20578.00")
    assert gift_capacity(Decimal("10000"), Decimal("12000")).capacity == Decimal("0.00")


def test_baseline_scenario_keeps_pcls_out_of_gift_capacity():
    scenario = default_planning_scenario("Baseline")
    plan = evaluate_scenario(scenario, ENGLAND_TAX_RULES["2026/27"])
    assert plan.tim.gift_capacity == Decimal("98047.00")
    assert plan.wendy.gift_capacity == Decimal("30578.00")
    assert plan.household_gift_capacity == Decimal("128625.00")
    assert plan.wendy_pcls == Decimal("50000.00")

    scenario.wendy_pcls = Decimal("100000")
    assert (
        evaluate_scenario(scenario, ENGLAND_TAX_RULES["2026/27"]).household_gift_capacity
        == Decimal("128625.00")
    )


def test_twenty_year_projection_tracks_pensions_and_state_pension_timing():
    scenario = default_planning_scenario("Timeline")
    rows = project_scenario(scenario, ENGLAND_TAX_RULES["2026/27"])
    assert len(rows) == 20
    assert rows[0].tax_year == "2027/28"
    assert rows[0].tim_age == 60
    assert rows[0].wendy_age == 54
    assert rows[0].wendy_pcls == Decimal("50000.00")
    assert rows[0].household_gift_capacity == Decimal("128625.00")
    assert rows[6].tim_state_pension > 0  # Tim reaches State Pension age in 2033/34.
    assert rows[5].tim_state_pension == Decimal("0")
    assert rows[-1].tim_sipp_end == Decimal("1283671.10")
    assert rows[-1].wendy_uncrystallised_end == Decimal("0.00")


def test_projection_uses_pcls_for_isa_before_recurring_income():
    scenario = default_planning_scenario("No PCLS")
    scenario.wendy_pcls = Decimal("0")
    without_pcls = project_scenario(scenario, ENGLAND_TAX_RULES["2026/27"])[0]
    scenario.wendy_pcls = Decimal("50000")
    with_pcls = project_scenario(scenario, ENGLAND_TAX_RULES["2026/27"])[0]
    assert without_pcls.isa_contributions_from_income == Decimal("40000.00")
    assert with_pcls.isa_contributions_from_pcls == Decimal("40000.00")
    assert with_pcls.isa_contributions_from_income == Decimal("0.00")
    assert (
        with_pcls.household_gift_capacity
        == without_pcls.household_gift_capacity + Decimal("40000.00")
    )
    assert with_pcls.wendy_sipp_end == without_pcls.wendy_sipp_end - Decimal("50000.00")


def test_isa_projection_shows_income_growth_contributions_and_balances():
    scenario = default_planning_scenario("ISAs")
    first = project_scenario(scenario, ENGLAND_TAX_RULES["2026/27"])[0]
    assert first.isa_income == Decimal("45500.00")
    assert first.tim_isa_growth == Decimal("9750.00")
    assert first.isa_contributions == Decimal("40000.00")
    assert first.tim_isa_end == Decimal("679750.00")
    assert first.wendy_isa_end == Decimal("679750.00")


def test_pension_actions_do_not_begin_before_configured_dates():
    scenario = default_planning_scenario("Delayed starts")
    scenario.tim_withdrawal_start = date(2028, 4, 6)
    scenario.wendy_first_crystallisation = date(2028, 5, 23)
    rows = project_scenario(scenario, ENGLAND_TAX_RULES["2026/27"])
    assert rows[0].tim_withdrawal == Decimal("0.00")
    assert rows[0].wendy_pcls == Decimal("0.00")
    assert rows[0].isa_contributions_from_income == Decimal("40000.00")
    assert rows[1].tim_withdrawal == Decimal("180000.00")
    assert rows[1].wendy_pcls == Decimal("50000.00")
