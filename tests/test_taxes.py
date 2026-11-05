"""The detailed tax engine, checked against hand arithmetic from the published
tables in tax_tables.py (federal 2026, California 2025). Inflation is 0 in
these tests so the tables apply as published.

Married filing jointly unless noted.
"""
import pytest

import simulation
import tax_tables as T
from migrations import migrate
from models import (
    Asset, Assumptions, Cashflow, CashflowDirection, Entity, FlowType, Loan, Ownership, Person, Portfolio,
    Scenario, TaxCategory,
)


def flow(fid, amount, ftype=FlowType.OTHER, cat=TaxCategory.ORDINARY_INCOME, out=False, **kw):
    return Cashflow(flow_id=fid, name=fid, flow_type=ftype,
                    direction=CashflowDirection.OUTFLOW if out else CashflowDirection.INFLOW,
                    amount=amount, start_year=kw.pop("start_year", 2026), taxable=not out,
                    tax_category=cat, **kw)


def wages(amount):
    return flow("wages", amount, FlowType.SALARY_INCOME)


def household(cashflows, assets=(), liabilities=(), people=(), years=1, events=(), **assumptions):
    return Portfolio(
        events=list(events),
        assumptions=Assumptions(start_year=2026, end_year=2025 + years, inflation_rate=0.0,
                                tax_method="detailed", **assumptions),
        assets=list(assets), cashflows=list(cashflows), liabilities=list(liabilities), people=list(people),
        scenarios=[Scenario(scenario_id="base", name="Base")],
    )


def taxes(p, year=2026):
    results = simulation.run_simulation(p, p.get_scenario("base")).annual_results
    return next(r for r in results if r.year == year).tax_estimate


def seniors():
    return [Person(person_id="a", name="A", birth_year=1955), Person(person_id="b", name="B", birth_year=1955)]


def rental(**kw):
    fields = dict(asset_id="rental", name="Rental", asset_type="real_estate", owner="",
                  initial_value=400_000, start_year=2026, tax_category=TaxCategory.RENTAL_INCOME)
    fields.update(kw)
    return Asset(**fields)


# --- the basics ---------------------------------------------------------------------------

def test_wage_earners_federal_fica_and_california():
    t = taxes(household([wages(100_000)]))
    d = t.details
    assert d["Taxable income"] == pytest.approx(100_000 - 32_200)          # 67,800
    assert t.tax_by_category["federal_income"] == pytest.approx(2_480 + 5_160)   # 10% + 12% slices
    assert t.tax_by_category["fica"] == pytest.approx(100_000 * 0.0765)    # 7,650
    # CA: 100,000 - 11,412 = 88,588 -> 2,044.02 + 6% x 5,684 = 2,385.06, less $306 credits
    assert t.tax_by_category["california"] == pytest.approx(2_385.06 - 306, abs=0.01)
    assert t.total_estimated_tax == pytest.approx(7_640 + 7_650 + 2_079.06, abs=0.01)


def test_no_state_tax_option():
    t = taxes(household([wages(100_000)], state="none"))
    assert t.tax_by_category["california"] == 0


def test_single_filer_uses_single_tables():
    t = taxes(household([wages(50_000)], filing_status="single"))
    # 50,000 - 16,100 = 33,900: 10% of 12,400 + 12% of 21,500
    assert t.tax_by_category["federal_income"] == pytest.approx(1_240 + 2_580)


# --- retirees: Social Security, 65+ deductions ----------------------------------------------------

def test_retired_couple_social_security_and_senior_deductions():
    p = household([flow("pension", 40_000, FlowType.PENSION_INCOME, TaxCategory.PENSION_INCOME),
                   flow("ss", 30_000, FlowType.SOCIAL_SECURITY_INCOME)], people=seniors())
    d = taxes(p).details
    # provisional = 40,000 + 15,000 = 55,000 > 44,000:
    # taxable SS = min(25,500, 0.85 x 11,000 + 6,000 = 15,350)
    assert d["Social Security taxable"] == pytest.approx(15_350)
    assert d["Standard deduction"] == pytest.approx(32_200 + 2 * 1_650)
    assert d["Senior deduction"] == pytest.approx(12_000)
    assert d["Taxable income"] == pytest.approx(55_350 - 35_500 - 12_000)   # 7,850 -> $785
    t = taxes(p)
    assert t.tax_by_category["federal_income"] == pytest.approx(785)
    assert t.tax_by_category["california"] == 0  # CA: no tax on SS; credits wipe out the rest


def test_senior_deduction_phases_out():
    p = household([flow("pension", 200_000, FlowType.PENSION_INCOME, TaxCategory.PENSION_INCOME)],
                  people=seniors())
    # each $6,000 less 6% of (200,000 - 150,000) = 3,000
    assert taxes(p).details["Senior deduction"] == pytest.approx(6_000)


def test_senior_deduction_ends_after_2028():
    p = household([flow("pension", 60_000, FlowType.PENSION_INCOME, TaxCategory.PENSION_INCOME)],
                  people=seniors(), years=4)
    assert taxes(p, 2028).details["Senior deduction"] == pytest.approx(12_000)
    assert taxes(p, 2029).details["Senior deduction"] == 0


# --- capital gains, NIIT, self-employment ------------------------------------------------------------

def test_capital_gains_stack_on_top_of_ordinary_income():
    t = taxes(household([wages(100_000), flow("gain", 50_000, cat=TaxCategory.CAPITAL_GAINS)]))
    d = t.details
    assert d["Federal tax on ordinary income"] == pytest.approx(7_640)
    # gains fill 67,800 -> 117,800: 31,100 at 0% (to 98,900), 18,900 at 15%
    assert d["Federal tax on capital gains"] == pytest.approx(18_900 * 0.15)


def test_gains_inside_the_zero_band_are_untaxed():
    t = taxes(household([flow("gain", 100_000, cat=TaxCategory.CAPITAL_GAINS)]))
    assert t.tax_by_category["federal_income"] == 0  # taxable 67,800 < 98,900


def test_net_investment_income_tax_and_additional_medicare():
    t = taxes(household([wages(300_000), flow("int", 50_000, FlowType.INTEREST_INCOME)]))
    assert t.tax_by_category["niit"] == pytest.approx(0.038 * 50_000)          # 1,900
    assert t.tax_by_category["additional_medicare"] == pytest.approx(0.009 * 50_000)  # 450


def test_consulting_pays_self_employment_tax_and_deducts_half():
    t = taxes(household([flow("consulting", 50_000, FlowType.CONSULTING_INCOME,
                              TaxCategory.SELF_EMPLOYMENT_INCOME)]))
    se = 50_000 * 0.9235 * 0.153
    assert t.tax_by_category["self_employment"] == pytest.approx(se)          # 7,064.78
    assert t.details["AGI"] == pytest.approx(50_000 - se / 2)


# --- rentals: depreciation and passive losses -------------------------------------------------------------

def test_depreciation_running_out_raises_taxable_income():
    house = rental(depreciable_basis=275_000, placed_in_service_year=2000, recovery_years=27.5)
    p = household([wages(100_000),
                   flow("rent", 30_000, FlowType.RENT_INCOME, TaxCategory.RENTAL_INCOME, asset_id="rental"),
                   flow("costs", 8_000, FlowType.OPERATING_EXPENSE, out=True, asset_id="rental")],
                  assets=[house], years=3)
    by_year = {y: taxes(p, y).details for y in (2026, 2027, 2028)}
    assert [by_year[y]["Depreciation"] for y in by_year] == pytest.approx([10_000, 5_000, 0])  # 27.5 yrs ends mid-2027
    assert [by_year[y]["Rental net (allowed this year)"] for y in by_year] == pytest.approx([12_000, 17_000, 22_000])
    assert by_year[2028]["Taxable income"] - by_year[2026]["Taxable income"] == pytest.approx(10_000)
    assert by_year[2028]["Federal tax on ordinary income"] - by_year[2026]["Federal tax on ordinary income"] \
        == pytest.approx(10_000 * 0.12)


def test_mortgage_interest_on_a_rental_is_deducted_from_its_income():
    p = household([flow("rent", 30_000, FlowType.RENT_INCOME, TaxCategory.RENTAL_INCOME, asset_id="rental")],
                  assets=[rental()],
                  liabilities=[Loan(liability_id="m", name="m", balance=100_000, annual_interest_rate=0.06,
                                    start_year=2026, term_months=360, secured_by_asset_id="rental")])
    t = taxes(p)
    interest = simulation.run_simulation(p, p.get_scenario("base")).annual_results[0].total_interest_paid
    assert t.details["Rental net (allowed this year)"] == pytest.approx(30_000 - interest)


def passive_case(wage, sell=False):
    house = rental(sale_year=2027 if sell else None)
    return household([wages(wage),
                      flow("rent26", 10_000, FlowType.RENT_INCOME, TaxCategory.RENTAL_INCOME, asset_id="rental",
                           end_year=2026),
                      flow("rent27", 45_000, FlowType.RENT_INCOME, TaxCategory.RENTAL_INCOME, asset_id="rental",
                           start_year=2027, end_year=2027),
                      flow("costs", 30_000, FlowType.OPERATING_EXPENSE, out=True, asset_id="rental")],
                     assets=[house], years=2)


def test_rental_loss_is_suspended_at_high_income_then_used_later():
    p = passive_case(200_000)
    y26, y27 = taxes(p, 2026).details, taxes(p, 2027).details
    assert y26["Rental net (before passive limits)"] == pytest.approx(-20_000)
    assert y26["Rental net (allowed this year)"] == 0                      # allowance gone above $150k
    assert y26["Suspended rental losses carried forward"] == pytest.approx(20_000)
    assert y27["Rental net (allowed this year)"] == 0                      # 15,000 profit absorbed
    assert y27["Suspended rental losses carried forward"] == pytest.approx(5_000)


def test_rental_loss_allowed_below_100k():
    d = taxes(passive_case(90_000), 2026).details
    assert d["Rental net (allowed this year)"] == pytest.approx(-20_000)
    assert d["Suspended rental losses carried forward"] == 0


def test_selling_the_rental_releases_suspended_losses():
    d = taxes(passive_case(200_000, sell=True), 2027).details
    assert d["Rental net (allowed this year)"] == pytest.approx(15_000 - 20_000)
    assert d["Suspended rental losses carried forward"] == 0


def test_your_share_of_depreciation_follows_ownership():
    p = Portfolio(
        assumptions=Assumptions(start_year=2026, end_year=2026, inflation_rate=0.0, tax_method="detailed"),
        entities=[Entity(entity_id="oakhill", name="Oak Hill", entity_type="llc"),
                  Entity(entity_id="owner", name="Owner", entity_type="joint")],
        ownerships=[Ownership(owner_entity_id="owner", owned_type="entity", owned_id="oakhill", percent=0.5)],
        assets=[rental(entity_id="oakhill", depreciable_basis=275_000, placed_in_service_year=2020,
                       recovery_years=27.5)],
        scenarios=[Scenario(scenario_id="base", name="Base")],
    )
    assert taxes(p).details["Depreciation"] == pytest.approx(10_000 * 0.5)  # half of 275,000 / 27.5


# --- itemizing --------------------------------------------------------------------------------------------------

def test_itemizing_with_home_mortgage_and_salt_cap():
    home = Asset(asset_id="home", name="Home", asset_type="real_estate", owner="", initial_value=900_000,
                 start_year=2026)
    p = household([wages(300_000), flow("prop_tax", 30_000, FlowType.PROPERTY_TAX, out=True, asset_id="home")],
                  assets=[home],
                  liabilities=[Loan(liability_id="m", name="m", balance=600_000, annual_interest_rate=0.06,
                                    start_year=2026, term_months=360, secured_by_asset_id="home",
                                    liability_type="mortgage")])
    d = taxes(p).details
    interest = simulation.run_simulation(p, p.get_scenario("base")).annual_results[0].total_interest_paid
    assert d["Itemized deductions"] == pytest.approx(40_400 + interest)   # SALT capped at $40,400
    assert d["Deduction used"] == d["Itemized deductions"]


def test_salt_cap_rules():
    assert T.salt_cap(2026, 300_000, 0) == 40_400
    assert T.salt_cap(2026, 606_333, 0) == pytest.approx(10_000, abs=1)   # phased down to the floor
    assert T.salt_cap(2030, 100_000, 0) == 10_000                         # cap reverts in 2030


# --- indexing, methods, migration ----------------------------------------------------------------------------

def test_later_years_index_brackets_by_inflation():
    first_bracket = T.indexed_brackets(T.FEDERAL_BRACKETS["married_joint"], 2026, 2027, 0.03)[0][0]
    assert first_bracket == pytest.approx(24_800 * 1.03)


def test_flat_method_still_available():
    p = household([wages(100_000)])
    p.assumptions.tax_method = "flat"
    assert taxes(p).total_estimated_tax == pytest.approx(100_000 * 0.24)


def test_existing_files_keep_the_flat_method(v0_1_data):
    assert migrate(v0_1_data)["assumptions"]["tax_method"] == "flat"


def test_new_portfolios_default_to_detailed():
    assert Assumptions(start_year=2026, end_year=2030).tax_method == "detailed"


# --- sales part-way through a year (a bug found while checking the example) ------------------------------
# A rental sold in January was charged a FULL year of depreciation and mortgage
# interest, inventing a large loss. Sales and payoffs now use the event's month.

def test_depreciation_stops_at_the_sale_month():
    house = rental(depreciable_basis=275_000, placed_in_service_year=2010, recovery_years=27.5,
                   sale_year=2030, sale_month=1)
    assert house.depreciation_in_year(2030) == 0                  # sold Jan 1: held 0 months
    house.sale_month = 7
    assert house.depreciation_in_year(2030) == pytest.approx(10_000 * 6 / 12)  # Jan-Jun


def test_sale_event_month_reaches_the_asset():
    from models import Event
    p = household([], assets=[rental(end_event_id="sell")],
                  events=[Event(event_id="sell", name="Sell", year=2030, month=4)])
    assert (p.get_asset("rental").sale_year, p.get_asset("rental").sale_month) == (2030, 4)


def test_loan_interest_stops_when_its_property_is_sold():
    from models import Event
    p = household([], assets=[rental(end_event_id="sell")], years=1,
                  events=[Event(event_id="sell", name="Sell", year=2026, month=4)],  # sold at the start of April
                  liabilities=[Loan(liability_id="m", name="m", balance=100_000, annual_interest_rate=0.06,
                                    start_year=2026, term_months=360, secured_by_asset_id="rental")])
    year = simulation.run_simulation(p, p.get_scenario("base")).annual_results[0].liability_results[0]
    three_months = Loan(liability_id="x", name="x", balance=100_000, annual_interest_rate=0.06,
                        start_year=2026, term_months=360).year_activity(100_000, months=3)[0]
    assert year.interest_paid == pytest.approx(three_months)      # Jan-Mar only
    assert year.paid_off_this_year


def test_credit_line_interest_for_part_of_a_year():
    from models import CreditLine
    heloc = CreditLine(liability_id="h", name="h", balance=60_000, annual_interest_rate=0.08, start_year=2026)
    assert heloc.year_activity(60_000, months=6)[0] == pytest.approx(2_400)
