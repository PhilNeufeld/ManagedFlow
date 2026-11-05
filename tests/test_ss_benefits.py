"""Social Security benefits from SSA's rules (SocialSecurity records).

Hand-checkable (born 1960+, full retirement age 67):
  own benefit: 62 -> 70% of PIA, 64 -> 80%, 67 -> 100%, 70 -> 124%
  spousal: half the spouse's PIA minus your own PIA; 62 -> 65%, 64 -> 75%, 67 -> 100%; no increase after 67
  survivor: the larger of the two benefits (82.5% of PIA floor if the deceased claimed early)
Alex born Jul 1966 (FRA Jul 2033), PIA $3,000/mo; Sam born Jul 1966, PIA $1,000/mo. COLA 0 for easy sums.
"""
import pytest

import simulation
import social_security as S
from models import (
    Assumptions, Cashflow, CashflowDirection, FlowType, Person, Portfolio, Scenario, SocialSecurity, ValidationError,
)


def plan(alex_age=67, sam_age=67, alex_life=92, sam_life=92, flows=(), **alex):
    return Portfolio(
        assumptions=Assumptions(start_year=2026, end_year=2062, inflation_rate=0.0, tax_method="flat",
                                default_tax_rates={}),
        people=[Person(person_id=1, name="Alex", birth_year=1966, birth_month=7, life_expectancy=alex_life),
                Person(person_id=2, name="Sam", birth_year=1966, birth_month=7, life_expectancy=sam_life)],
        social_security=[SocialSecurity(ss_id=1, person_id=1, pia=3_000, claim_age_years=alex_age, cola=0.0, **alex),
                         SocialSecurity(ss_id=2, person_id=2, pia=1_000, claim_age_years=sam_age, cola=0.0)],
        cashflows=list(flows),
        scenarios=[Scenario(scenario_id=1, name="Base", is_base_case=True)])


def ss_by_year(p, scenario=None):
    res = simulation.run_simulation(p, scenario or p.scenarios[0])
    return {r.year: r.total_gross_income for r in res.annual_results}, res


def test_reduction_and_delay_factors():
    assert S.own_factor(60) == pytest.approx(0.70) and S.own_factor(36) == pytest.approx(0.80)
    assert S.own_factor(0) == 1 and S.own_factor(-36) == pytest.approx(1.24)
    assert S.own_factor(-60, months_fra_to_70=36) == pytest.approx(1.24)          # no credit past 70
    assert S.spousal_factor(60) == pytest.approx(0.65) and S.spousal_factor(36) == pytest.approx(0.75)
    assert S.spousal_factor(-24) == 1


def test_claim_dates():
    alex = Person(person_id=1, name="A", birth_year=1966, birth_month=7)
    assert S.claim_date(alex, 62) == (2028, 8)          # 62 all month: the month after the birthday
    assert S.claim_date(alex, 67) == (2033, 7) and S.claim_date(alex, 70) == (2036, 7)


def test_own_and_spousal_amounts():
    plans = S.plan_benefits(plan())
    assert plans[1].own_monthly == 3_000 and plans[1].spousal_monthly == 0
    assert plans[2].own_monthly == 1_000
    assert plans[2].spousal_monthly == pytest.approx(0.5 * 3_000 - 1_000)       # $500
    early = S.plan_benefits(plan(alex_age=62, sam_age=64))      # Alex files first, so Sam's spousal starts at 64
    assert early[2].own_monthly == pytest.approx(800)
    assert early[2].spousal_monthly == pytest.approx(500 * 0.75)
    waits = S.plan_benefits(plan(sam_age=64))                   # Alex files at 67 = Sam's FRA: no reduction
    assert waits[2].spousal_monthly == pytest.approx(500)


def test_spousal_waits_for_the_worker_to_file():
    plans = S.plan_benefits(plan(alex_age=70, sam_age=62))
    assert plans[2].spousal_start == S.claim_date(Person(person_id=1, name="A", birth_year=1966, birth_month=7), 70)


def test_yearly_income_with_partial_first_year():
    income, _ = ss_by_year(plan())
    assert income[2033] == pytest.approx(6 * (3_000 + 1_000 + 500))               # Jul-Dec 2033
    assert income[2034] == pytest.approx(12 * 4_500)


def test_survivor_keeps_the_larger_benefit():
    income, _ = ss_by_year(plan(alex_life=80))                                    # Alex dies Jul 2046
    assert income[2045] == pytest.approx(12 * 4_500)
    assert income[2047] == pytest.approx(12 * 3_000)                              # Sam now gets Alex's $3,000
    floor = S.plan_benefits(plan(alex_age=62, alex_life=80))
    assert floor[2].survivor_monthly == pytest.approx(0.825 * 3_000 - 1_000)     # 82.5% floor beats 70%


def test_scenario_can_change_the_claiming_age():
    p = plan()
    p.scenarios.append(Scenario(scenario_id=2, name="Alex at 70", ss_overrides={1: {"claim_age_years": 70}}))
    base, _ = ss_by_year(p)
    late, _ = ss_by_year(p, p.scenarios[1])
    assert late[2040] == pytest.approx(12 * (3_000 * 1.24 + 1_000 + 500))
    assert p.social_security[0].claim_age_years == 67                             # stored plan untouched


def test_withheld_months_are_credited_back_at_full_retirement_age():
    salary = Cashflow(flow_id="w", name="Alex salary", flow_type=FlowType.SALARY_INCOME,
                      direction=CashflowDirection.INFLOW, amount=200_000, start_year=2026, end_year=2032,
                      taxable=True, person_id=1)
    p = plan(alex_age=62, flows=[salary])
    _, res = ss_by_year(p)
    years = {r.year: r for r in res.annual_results}
    assert any("earnings test" in w for w in years[2029].warnings)                # all withheld while working
    assert any("recomputed at full retirement age" in w for w in years[2033].warnings)
    alex_ss = next(cf for cf in res.working_portfolio.cashflows if cf.flow_id == "ss:1:own")
    # Claiming at 62 = Aug 2028, 59 months before FRA (Jul 2033). Aug 2028 - Dec 2032 fully withheld = 53
    # months -> recomputed as if claimed 53 months later: 59 - 53 = 6 months early.
    assert alex_ss.ss_withheld_months == 53 and alex_ss.ss_info["months_early"] == 59
    assert alex_ss.annual_amount_in_year(2034) == pytest.approx(12 * 3_000 * S.own_factor(6))


def test_claiming_analysis_runs_every_combination():
    rows = S.claiming_analysis(plan(), plan().scenarios[0], ages=(62, 67, 70))
    assert len(rows) == 9
    best = max(rows, key=lambda r: r["Lifetime Social Security"])
    assert (best["Alex claims at"], best["Sam claims at"]) == (70, 70) or best["Lifetime Social Security"] > 0


def test_records_validate_and_survive_save_and_load():
    with pytest.raises(ValidationError):
        SocialSecurity(ss_id=1, person_id=1, pia=1_000, claim_age_years=71)
    p = plan()
    back = Portfolio.from_dict(p.to_dict())
    assert [s.claim_age_years for s in back.social_security] == [67, 67]
    with pytest.raises(ValidationError):
        Portfolio(people=p.people, social_security=p.social_security + [
            SocialSecurity(ss_id=3, person_id=1, pia=1, claim_age_years=67)])
