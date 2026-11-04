"""Per-person payroll / self-employment tax and the Social Security earnings test.
Inflation is 0 so limits after 2027 equal 2027's. Numbers to check by hand:

  2026 wage base $184,500 per PERSON (2027: $192,300).
  Phil wages $126,327.57, Debbie $81,456 -> FICA each on their own wages:
      207,783.57 x 7.65% = $15,895.44   (a combined cap would give only $14,452)
  Earnings test 2027-on: $25,560 under FRA ($1 per $2 over); $67,920 in the FRA year ($1 per $3).
  Phil (born Feb 1965) reaches full retirement age 67 in Feb 2032.
"""
import pytest

import simulation
import tax_tables as T
from models import (
    Actual, Assumptions, Cashflow, CashflowDirection, FlowType, Frequency, Person, Portfolio, Scenario,
)

IN = CashflowDirection.INFLOW


def plan(*flows, start=2026, end=2033, substantial=True, actuals=()):
    return Portfolio(
        assumptions=Assumptions(start_year=start, end_year=end, inflation_rate=0.0, tax_method="detailed"),
        people=[Person(person_id="phil", name="Phil", birth_year=1965, birth_month=2,
                       substantial_self_employment=substantial),
                Person(person_id="debbie", name="Debbie", birth_year=1964, birth_month=10)],
        cashflows=list(flows), actuals=list(actuals),
        scenarios=[Scenario(scenario_id="base", name="Base", is_base_case=True)],
    )


def wages(fid, who, amount, start=2026, end=None, end_month=12):
    return Cashflow(flow_id=fid, name=fid, flow_type=FlowType.SALARY_INCOME, direction=IN, amount=amount,
                    start_year=start, end_year=end, end_month=end_month, taxable=True, person_id=who)


def se(fid, who, amount, start=2026):
    return Cashflow(flow_id=fid, name=fid, flow_type=FlowType.MANAGEMENT_FEE, direction=IN, amount=amount,
                    start_year=start, taxable=True, tax_category="self_employment_income", person_id=who)


def ss(fid, who, monthly, year, month):
    return Cashflow(flow_id=fid, name=fid, flow_type=FlowType.SOCIAL_SECURITY_INCOME, direction=IN,
                    amount=monthly, frequency=Frequency.MONTHLY, start_year=year, start_month=month,
                    person_id=who)


def year_result(p, year):
    return next(r for r in simulation.run_simulation(p, p.get_scenario("base")).annual_results if r.year == year)


# --- payroll and self-employment tax per person ----------------------------------------------------

def test_fica_uses_each_spouses_own_wage_base():
    p = plan(wages("phil_w", "phil", 126_327.57, end=2026), wages("deb_w", "debbie", 81_456, end=2026), end=2026)
    d = year_result(p, 2026).tax_estimate.details
    assert d["FICA"] == pytest.approx(207_783.57 * 0.0765, abs=0.01)
    assert d["FICA - Phil"] == pytest.approx(126_327.57 * 0.0765, abs=0.01)


def test_self_employment_fills_only_its_owners_remaining_wage_base():
    # Phil: $180,000 wages leaves $4,500 of the 2026 base; $50,000 SE -> NESE 46,175
    phil_se = plan(wages("w", "phil", 180_000), se("fee", "phil", 50_000), end=2026)
    d = year_result(phil_se, 2026).tax_estimate.details
    assert d["Self-employment tax - Phil"] == pytest.approx(4_500 * 0.124 + 46_175 * 0.029, abs=0.01)
    # same fee earned by Debbie (no wages): the whole NESE pays Social Security tax
    deb_se = plan(wages("w", "phil", 180_000), se("fee", "debbie", 50_000), end=2026)
    d = year_result(deb_se, 2026).tax_estimate.details
    assert d["Self-employment tax - Debbie"] == pytest.approx(46_175 * 0.153, abs=0.01)


def test_wage_base_by_year():
    assert T.ss_wage_base(2026, 0.03) == 184_500
    assert T.ss_wage_base(2027, 0.03) == 192_300
    assert T.ss_wage_base(2028, 0.03) == pytest.approx(192_300 * 1.03)
    assert T.earnings_test_limits(2027, 0.0) == (25_560, 67_920)


def test_unassigned_wages_count_as_the_first_person():
    flow = wages("w", None, 200_000)
    d = year_result(plan(flow, end=2026), 2026).tax_estimate.details
    assert d["FICA - Phil"] == pytest.approx(184_500 * 0.062 + 200_000 * 0.0145)


# --- full retirement age ------------------------------------------------------------------------------

def test_full_retirement_age_by_birth_year():
    assert Person(person_id="a", name="a", birth_year=1965, birth_month=2).full_retirement_date() == (2032, 2)
    p57 = Person(person_id="b", name="b", birth_year=1957, birth_month=11)
    assert p57.full_retirement_age() == (66, 6) and p57.full_retirement_date() == (2024, 5)


# --- the earnings test ----------------------------------------------------------------------------------

def test_under_fra_one_dollar_withheld_per_two_over_the_limit():
    p = plan(wages("w", "phil", 60_000, start=2026), ss("phil_ss", "phil", 35_000 / 12, 2027, 1))
    r = year_result(p, 2028)
    assert r.total_gross_income == pytest.approx(60_000 + 35_000 - (60_000 - 25_560) / 2)
    assert any("earnings test" in w for w in r.warnings)


def test_withholding_never_exceeds_the_benefit():
    p = plan(se("fee", "phil", 200_000), ss("phil_ss", "phil", 35_000 / 12, 2027, 3))
    r = year_result(p, 2028)
    assert r.total_gross_income == pytest.approx(200_000)       # whole $35,000 withheld


def test_grace_year_pays_retired_months_after_wages_stop():
    # Debbie: wages $81,456 through June 2028, claims Oct 2028 -> annual earnings $40,728 are
    # over the limit, but Oct-Dec are retired months, so all three are paid
    p = plan(wages("deb_w", "debbie", 81_456 / 12, end=2028, end_month=6),
             ss("deb_ss", "debbie", 1_250, 2028, 10))
    p.cashflows[0].frequency = Frequency.MONTHLY
    r = year_result(p, 2028)
    assert r.total_gross_income == pytest.approx(81_456 / 2 + 3 * 1_250)
    assert not any("earnings test" in w for w in r.warnings)


def test_grace_year_and_self_employment_depend_on_substantial_services():
    busy = plan(se("fee", "phil", 120_000), ss("phil_ss", "phil", 2_000, 2027, 3))
    assert year_result(busy, 2027).total_gross_income == pytest.approx(120_000)   # all 10 months withheld
    idle = plan(se("fee", "phil", 120_000), ss("phil_ss", "phil", 2_000, 2027, 3), substantial=False)
    assert year_result(idle, 2027).total_gross_income == pytest.approx(120_000 + 20_000)


def test_fra_year_counts_only_earnings_before_fra_and_the_higher_limit():
    # 2032: FRA in Feb, so only January counts: $120,000/12 = $10,000 < $67,920 -> nothing withheld
    p = plan(se("fee", "phil", 120_000), ss("phil_ss", "phil", 3_000, 2027, 3))
    assert year_result(p, 2032).total_gross_income == pytest.approx(120_000 + 36_000)
    assert year_result(p, 2033).total_gross_income == pytest.approx(120_000 + 36_000)


def test_benefit_recorded_as_an_actual_is_left_alone():
    rec = Actual(actual_id="a", year=2028, amount=35_000, name="Phil SS", flow_id="phil_ss",
                 category="social_security_income")
    p = plan(se("fee", "phil", 200_000), ss("phil_ss", "phil", 35_000 / 12, 2027, 1), actuals=[rec])
    assert year_result(p, 2028).total_gross_income == pytest.approx(235_000)
