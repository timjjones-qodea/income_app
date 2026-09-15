from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


ZERO = Decimal("0")
PENNY = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(PENNY, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class EnglandTaxRules:
    tax_year: str
    personal_allowance: Decimal
    allowance_taper_start: Decimal
    basic_rate_band: Decimal
    additional_rate_threshold: Decimal
    basic_rate: Decimal
    higher_rate: Decimal
    additional_rate: Decimal


ENGLAND_TAX_RULES = {
    "2025/26": EnglandTaxRules(
        tax_year="2025/26",
        personal_allowance=Decimal("12570"),
        allowance_taper_start=Decimal("100000"),
        basic_rate_band=Decimal("37700"),
        additional_rate_threshold=Decimal("125140"),
        basic_rate=Decimal("0.20"),
        higher_rate=Decimal("0.40"),
        additional_rate=Decimal("0.45"),
    ),
    "2026/27": EnglandTaxRules(
        tax_year="2026/27",
        personal_allowance=Decimal("12570"),
        allowance_taper_start=Decimal("100000"),
        basic_rate_band=Decimal("37700"),
        additional_rate_threshold=Decimal("125140"),
        basic_rate=Decimal("0.20"),
        higher_rate=Decimal("0.40"),
        additional_rate=Decimal("0.45"),
    ),
}


@dataclass(frozen=True)
class IncomeTaxResult:
    adjusted_net_income: Decimal
    personal_allowance: Decimal
    taxable_income: Decimal
    income_tax_before_credit: Decimal
    foreign_tax_credit: Decimal
    income_tax_due: Decimal


def england_non_savings_income_tax(
    gross_income: Decimal,
    rules: EnglandTaxRules,
    *,
    gross_pension_contributions: Decimal = ZERO,
    foreign_income: Decimal = ZERO,
    foreign_tax_paid: Decimal = ZERO,
) -> IncomeTaxResult:
    """Planning calculation for non-savings, non-dividend income.

    It intentionally exposes assumptions and is not a Self Assessment engine.
    Foreign-tax credit is conservatively capped at the lower of foreign tax paid
    and the UK tax attributable to the foreign-income slice.
    """
    gross_income = max(ZERO, Decimal(gross_income))
    gross_pension_contributions = max(ZERO, Decimal(gross_pension_contributions))
    adjusted_net_income = max(ZERO, gross_income - gross_pension_contributions)
    taper = max(ZERO, adjusted_net_income - rules.allowance_taper_start) / Decimal("2")
    allowance = max(ZERO, rules.personal_allowance - taper)
    taxable = max(ZERO, gross_income - allowance)

    extended_basic_band = rules.basic_rate_band + gross_pension_contributions
    basic = min(taxable, extended_basic_band)
    higher_limit = (
        rules.additional_rate_threshold - allowance + gross_pension_contributions
    )
    higher = min(
        max(ZERO, taxable - basic), max(ZERO, higher_limit - extended_basic_band)
    )
    additional = max(ZERO, taxable - basic - higher)
    tax_before_credit = money(
        basic * rules.basic_rate
        + higher * rules.higher_rate
        + additional * rules.additional_rate
    )

    foreign_income = min(max(ZERO, Decimal(foreign_income)), gross_income)
    foreign_tax_paid = max(ZERO, Decimal(foreign_tax_paid))
    if foreign_income:
        domestic_only = england_non_savings_income_tax(
            gross_income - foreign_income,
            rules,
            gross_pension_contributions=gross_pension_contributions,
        ).income_tax_before_credit
        attributable_uk_tax = max(ZERO, tax_before_credit - domestic_only)
        credit = money(min(foreign_tax_paid, attributable_uk_tax))
    else:
        credit = ZERO

    return IncomeTaxResult(
        adjusted_net_income=money(adjusted_net_income),
        personal_allowance=money(allowance),
        taxable_income=money(taxable),
        income_tax_before_credit=tax_before_credit,
        foreign_tax_credit=credit,
        income_tax_due=money(tax_before_credit - credit),
    )


@dataclass(frozen=True)
class GiftCapacity:
    net_income: Decimal
    normal_expenditure: Decimal
    safety_margin: Decimal
    capacity: Decimal


def gift_capacity(
    net_income: Decimal,
    normal_expenditure: Decimal,
    safety_margin: Decimal = ZERO,
) -> GiftCapacity:
    net_income = money(max(ZERO, Decimal(net_income)))
    normal_expenditure = money(max(ZERO, Decimal(normal_expenditure)))
    safety_margin = money(max(ZERO, Decimal(safety_margin)))
    return GiftCapacity(
        net_income=net_income,
        normal_expenditure=normal_expenditure,
        safety_margin=safety_margin,
        capacity=money(max(ZERO, net_income - normal_expenditure - safety_margin)),
    )


@dataclass(frozen=True)
class PersonPlan:
    gross_taxable_income: Decimal
    income_tax: Decimal
    foreign_tax: Decimal
    isa_income: Decimal
    recurring_net_income: Decimal
    normal_expenditure: Decimal
    safety_margin: Decimal
    gift_capacity: Decimal


@dataclass(frozen=True)
class ScenarioPlan:
    tim: PersonPlan
    wendy: PersonPlan
    household_recurring_net_income: Decimal
    household_expenditure: Decimal
    household_gift_capacity: Decimal
    wendy_pcls: Decimal
    isa_subscriptions: Decimal


@dataclass(frozen=True)
class ProjectionYear:
    tax_year: str
    tim_age: int
    wendy_age: int
    tim_state_pension: Decimal
    wendy_state_pension: Decimal
    tim_withdrawal: Decimal
    wendy_pcls: Decimal
    household_expenditure: Decimal
    household_gift_capacity: Decimal
    tim_sipp_end: Decimal
    wendy_sipp_end: Decimal
    wendy_uncrystallised_end: Decimal
    wendy_lump_sum_allowance_end: Decimal
    isa_income: Decimal
    isa_contributions: Decimal
    isa_contributions_from_pcls: Decimal
    isa_contributions_from_income: Decimal
    tim_isa_open: Decimal
    wendy_isa_open: Decimal
    tim_isa_growth: Decimal
    wendy_isa_growth: Decimal
    tim_isa_end: Decimal
    wendy_isa_end: Decimal


def age_on(value: date, on_date: date) -> int:
    return on_date.year - value.year - ((on_date.month, on_date.day) < (value.month, value.day))


def state_pension_in_tax_year(start: date, start_year: int, annual_amount: Decimal) -> Decimal:
    period_start = date(start_year, 4, 6)
    period_end = date(start_year + 1, 4, 5)
    if start <= period_start:
        return money(annual_amount)
    if start > period_end:
        return ZERO
    days_in_period = Decimal((period_end - period_start).days + 1)
    payable_days = Decimal((period_end - start).days + 1)
    return money(annual_amount * payable_days / days_in_period)


def evaluate_scenario(scenario, rules: EnglandTaxRules) -> ScenarioPlan:
    """Evaluate a persisted scenario without treating capital as giftable income."""
    isa_yield = Decimal(scenario.isa_yield_percent) / Decimal("100")
    tim_isa_income = money(Decimal(scenario.tim_isa_value) * isa_yield)
    wendy_isa_income = money(Decimal(scenario.wendy_isa_value) * isa_yield)

    tim_tax = england_non_savings_income_tax(
        Decimal(scenario.tim_pension_withdrawal), rules
    )
    tim_net = money(
        Decimal(scenario.tim_pension_withdrawal) - tim_tax.income_tax_due + tim_isa_income
    )

    wendy_gross = money(
        Decimal(scenario.wendy_uk_property_profit)
        + Decimal(scenario.wendy_french_property_gross)
        + Decimal(scenario.wendy_sole_trade_profit)
    )
    wendy_tax = england_non_savings_income_tax(
        wendy_gross,
        rules,
        foreign_income=Decimal(scenario.wendy_french_property_gross),
        foreign_tax_paid=Decimal(scenario.wendy_french_tax_paid),
    )
    wendy_net = money(
        wendy_gross
        - Decimal(scenario.wendy_french_tax_paid)
        - wendy_tax.income_tax_due
        + wendy_isa_income
    )

    household_expenditure = money(Decimal(scenario.household_expenditure))
    tim_expenses = money(
        household_expenditure
        * Decimal(scenario.tim_expense_share_percent)
        / Decimal("100")
    )
    wendy_expenses = money(household_expenditure - tim_expenses)
    isa_subscriptions = money(
        min(
            Decimal(scenario.isa_contribution_per_person),
            Decimal(scenario.isa_allowance_per_person),
        )
        * Decimal("2")
    )
    income_funded_subscriptions = max(
        ZERO, isa_subscriptions - Decimal(scenario.wendy_pcls)
    )
    tim_income_funded_isa = money(income_funded_subscriptions / Decimal("2"))
    tim_capacity = gift_capacity(
        tim_net,
        tim_expenses + tim_income_funded_isa,
        Decimal(scenario.tim_safety_margin),
    )
    wendy_capacity = gift_capacity(
        wendy_net,
        wendy_expenses + (income_funded_subscriptions - tim_income_funded_isa),
        Decimal(scenario.wendy_safety_margin),
    )

    tim = PersonPlan(
        gross_taxable_income=money(Decimal(scenario.tim_pension_withdrawal)),
        income_tax=tim_tax.income_tax_due,
        foreign_tax=ZERO,
        isa_income=tim_isa_income,
        recurring_net_income=tim_net,
        normal_expenditure=tim_expenses,
        safety_margin=money(Decimal(scenario.tim_safety_margin)),
        gift_capacity=tim_capacity.capacity,
    )
    wendy = PersonPlan(
        gross_taxable_income=wendy_gross,
        income_tax=wendy_tax.income_tax_due,
        foreign_tax=money(Decimal(scenario.wendy_french_tax_paid)),
        isa_income=wendy_isa_income,
        recurring_net_income=wendy_net,
        normal_expenditure=wendy_expenses,
        safety_margin=money(Decimal(scenario.wendy_safety_margin)),
        gift_capacity=wendy_capacity.capacity,
    )
    return ScenarioPlan(
        tim=tim,
        wendy=wendy,
        household_recurring_net_income=money(tim_net + wendy_net),
        household_expenditure=household_expenditure,
        household_gift_capacity=money(tim_capacity.capacity + wendy_capacity.capacity),
        wendy_pcls=money(Decimal(scenario.wendy_pcls)),
        isa_subscriptions=isa_subscriptions,
    )


def project_scenario(scenario, rules: EnglandTaxRules) -> list[ProjectionYear]:
    """Project nominal annual cash flow using one explicitly selected tax-rule set."""
    rows: list[ProjectionYear] = []
    growth = Decimal("1") + Decimal(scenario.investment_growth_percent) / Decimal("100")
    inflation = Decimal("1") + Decimal(scenario.inflation_percent) / Decimal("100")
    state_growth = Decimal("1") + Decimal(scenario.state_pension_growth_percent) / Decimal("100")
    tim_crystallised = Decimal(scenario.tim_sipp_crystallised)
    tim_uncrystallised = Decimal(scenario.tim_sipp_uncrystallised)
    wendy_crystallised = Decimal(scenario.wendy_sipp_crystallised)
    wendy_uncrystallised = Decimal(scenario.wendy_sipp_uncrystallised)
    lump_sum_remaining = Decimal(scenario.wendy_lump_sum_allowance)
    isa_yield = Decimal(scenario.isa_yield_percent) / Decimal("100")
    isa_capital_growth = (
        Decimal("1") + Decimal(scenario.isa_capital_growth_percent) / Decimal("100")
    )
    tim_isa = Decimal(scenario.tim_isa_value)
    wendy_isa = Decimal(scenario.wendy_isa_value)
    isa_contribution_each = min(
        Decimal(scenario.isa_contribution_per_person),
        Decimal(scenario.isa_allowance_per_person),
    )

    for index in range(int(scenario.projection_years)):
        year = int(scenario.projection_start_year) + index
        tax_year = f"{year}/{str(year + 1)[-2:]}"
        period_start = date(year, 4, 6)
        period_end = date(year + 1, 4, 5)
        tim_crystallised *= growth
        tim_uncrystallised *= growth
        wendy_crystallised *= growth
        wendy_uncrystallised *= growth

        requested_withdrawal = (
            Decimal(scenario.tim_pension_withdrawal)
            if scenario.tim_withdrawal_start <= period_end
            else ZERO
        )
        tim_withdrawal = min(requested_withdrawal, tim_crystallised + tim_uncrystallised)
        from_crystallised = min(tim_crystallised, tim_withdrawal)
        tim_crystallised -= from_crystallised
        tim_uncrystallised -= tim_withdrawal - from_crystallised

        crystallisation = (
            min(Decimal(scenario.wendy_annual_crystallisation), wendy_uncrystallised)
            if scenario.wendy_first_crystallisation <= period_end
            else ZERO
        )
        wendy_pcls = min(
            Decimal(scenario.wendy_pcls), crystallisation * Decimal("0.25"), lump_sum_remaining
        )
        wendy_uncrystallised -= crystallisation
        wendy_crystallised += crystallisation - wendy_pcls
        lump_sum_remaining -= wendy_pcls

        indexed_state_pension = Decimal(scenario.state_pension_annual) * (
            state_growth ** Decimal(year - 2026)
        )
        tim_state = state_pension_in_tax_year(
            scenario.tim_state_pension_start, year, indexed_state_pension
        )
        wendy_state = state_pension_in_tax_year(
            scenario.wendy_state_pension_start, year, indexed_state_pension
        )

        tim_isa_open = tim_isa
        wendy_isa_open = wendy_isa
        tim_isa_income = money(tim_isa_open * isa_yield)
        wendy_isa_income = money(wendy_isa_open * isa_yield)
        tim_isa_growth = money(tim_isa_open * (isa_capital_growth - Decimal("1")))
        wendy_isa_growth = money(wendy_isa_open * (isa_capital_growth - Decimal("1")))
        total_isa_contributions = money(isa_contribution_each * Decimal("2"))
        contribution_from_pcls = min(wendy_pcls, total_isa_contributions)
        contribution_from_income = total_isa_contributions - contribution_from_pcls
        income_contribution_each = money(contribution_from_income / Decimal("2"))

        tim_gross = tim_withdrawal + tim_state
        tim_tax = england_non_savings_income_tax(tim_gross, rules)
        tim_net = money(tim_gross - tim_tax.income_tax_due + tim_isa_income)
        wendy_other_gross = (
            Decimal(scenario.wendy_uk_property_profit)
            + Decimal(scenario.wendy_french_property_gross)
            + Decimal(scenario.wendy_sole_trade_profit)
        )
        wendy_gross = wendy_other_gross + wendy_state
        wendy_tax = england_non_savings_income_tax(
            wendy_gross,
            rules,
            foreign_income=Decimal(scenario.wendy_french_property_gross),
            foreign_tax_paid=Decimal(scenario.wendy_french_tax_paid),
        )
        wendy_net = money(
            wendy_gross
            - Decimal(scenario.wendy_french_tax_paid)
            - wendy_tax.income_tax_due
            + wendy_isa_income
        )

        expense_base = (
            Decimal(scenario.household_expenditure)
            if index < int(scenario.high_spend_years)
            else Decimal(scenario.later_household_expenditure)
        )
        expenditure = money(expense_base * (inflation ** Decimal(index)))
        tim_expense = money(
            expenditure * Decimal(scenario.tim_expense_share_percent) / Decimal("100")
        )
        wendy_expense = expenditure - tim_expense
        tim_capacity = gift_capacity(
            tim_net,
            tim_expense + income_contribution_each,
            Decimal(scenario.tim_safety_margin),
        )
        wendy_capacity = gift_capacity(
            wendy_net,
            wendy_expense + (contribution_from_income - income_contribution_each),
            Decimal(scenario.wendy_safety_margin),
        )
        tim_isa = tim_isa_open + tim_isa_growth + isa_contribution_each
        wendy_isa = wendy_isa_open + wendy_isa_growth + isa_contribution_each
        on_date = period_start
        rows.append(
            ProjectionYear(
                tax_year=tax_year,
                tim_age=age_on(date(1966, 11, 13), on_date),
                wendy_age=age_on(date(1972, 5, 23), on_date),
                tim_state_pension=tim_state,
                wendy_state_pension=wendy_state,
                tim_withdrawal=money(tim_withdrawal),
                wendy_pcls=money(wendy_pcls),
                household_expenditure=expenditure,
                household_gift_capacity=money(tim_capacity.capacity + wendy_capacity.capacity),
                tim_sipp_end=money(tim_crystallised + tim_uncrystallised),
                wendy_sipp_end=money(wendy_crystallised + wendy_uncrystallised),
                wendy_uncrystallised_end=money(wendy_uncrystallised),
                wendy_lump_sum_allowance_end=money(lump_sum_remaining),
                isa_income=money(tim_isa_income + wendy_isa_income),
                isa_contributions=total_isa_contributions,
                isa_contributions_from_pcls=money(contribution_from_pcls),
                isa_contributions_from_income=money(contribution_from_income),
                tim_isa_open=money(tim_isa_open),
                wendy_isa_open=money(wendy_isa_open),
                tim_isa_growth=tim_isa_growth,
                wendy_isa_growth=wendy_isa_growth,
                tim_isa_end=money(tim_isa),
                wendy_isa_end=money(wendy_isa),
            )
        )
    return rows
