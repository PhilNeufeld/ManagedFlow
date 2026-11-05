"""How accounts are taxed: taxable / tax-deferred / Roth; RMDs; withdrawal order; gross-up.

Hand-checkable: an owner born 1951 is 75 in 2026; RMDs start at 73 for 1951-59 births
(75 for 1960+). Uniform Lifetime factor at 75 = 24.6, so a $246,000 IRA (0% growth) must
pay out $246,000 / 24.6 = $10,000 in 2026, as ordinary income.
"""
import pytest

import simulation
import tax_tables as T
from models import Asset, Assumptions, Cashflow, CashflowDirection, FlowType, Person, Portfolio, Scenario


def account(aid, kind, value, owner="ann", liquidity="liquid"):
    return Asset(asset_id=aid, name=aid, asset_type="equity_fund", owner="Ann", initial_value=value, start_year=2026,
                 account_tax=kind, owner_person_id=owner, liquidity_category=liquidity)


def plan(*assets, born=1951, spend=0.0, tax="detailed"):
    flows = [Cashflow(flow_id="spend", name="Spending", flow_type=FlowType.LIVING_EXPENSE,
                      direction=CashflowDirection.OUTFLOW, amount=spend, start_year=2026)] if spend else []
    return Portfolio(
        assumptions=Assumptions(start_year=2026, end_year=2028, inflation_rate=0.0, tax_method=tax,
                                filing_status="single", default_tax_rates={"ordinary_income": 0.2}),
        people=[Person(person_id="ann", name="Ann", birth_year=born, birth_month=1)],
        assets=list(assets), cashflows=flows,
        scenarios=[Scenario(scenario_id="base", name="Base", is_base_case=True, reinvest_surplus=False)])


def year(p, y=2026):
    return next(r for r in simulation.run_simulation(p, p.scenarios[0]).annual_results if r.year == y)


def ending(r, aid):
    return next(a.ending_value for a in r.asset_results if a.asset_id == aid)


def test_rmd_ages_and_factors():
    assert (T.rmd_start_age(1950), T.rmd_start_age(1955), T.rmd_start_age(1960)) == (72, 73, 75)
    assert T.rmd_factor(75) == 24.6 and T.rmd_factor(90) == 12.2


def test_required_minimum_distribution_is_taken_and_taxed():
    r = year(plan(account("ira", "tax_deferred", 246_000)))
    assert r.total_gross_income == pytest.approx(10_000)
    assert ending(r, "ira") == pytest.approx(236_000)
    assert r.tax_estimate.details["Other ordinary income"] == pytest.approx(10_000)
    nxt = year(plan(account("ira", "tax_deferred", 246_000)), 2027)          # age 76: 236,000 / 23.7
    assert nxt.total_gross_income == pytest.approx(236_000 / 23.7)


def test_no_rmd_before_the_start_age_or_from_a_roth():
    assert year(plan(account("ira", "tax_deferred", 246_000), born=1960)).total_gross_income == 0
    assert year(plan(account("roth", "roth", 246_000))).total_gross_income == 0


def test_shortfalls_draw_taxable_then_tax_deferred_then_roth():
    p = plan(account("roth", "roth", 100_000), account("ira", "tax_deferred", 100_000),
             account("brk", "taxable", 30_000), born=1970, spend=50_000, tax="flat")
    r = year(p)
    assert r.withdrawal_breakdown["brk"] == pytest.approx(30_000)
    assert "roth" not in r.withdrawal_breakdown
    # the other $20,000 comes from the IRA, grossed up for 20% flat tax: 20,000 / 0.8 = 25,000
    assert r.withdrawal_breakdown["ira"] == pytest.approx(25_000, abs=1)
    assert r.total_estimated_tax == pytest.approx(5_000, abs=1)


def test_roth_and_taxable_withdrawals_are_not_income():
    r = year(plan(account("roth", "roth", 100_000), born=1970, spend=40_000, tax="flat"))
    assert r.withdrawal_breakdown["roth"] == pytest.approx(40_000) and r.total_estimated_tax == 0


def test_detailed_gross_up_covers_the_tax_on_the_withdrawal():
    r = year(plan(account("ira", "tax_deferred", 500_000), born=1970, spend=80_000))
    out = r.withdrawal_breakdown["ira"]
    assert out == pytest.approx(80_000 + r.total_estimated_tax, abs=1)         # spending + its own tax
    assert r.tax_estimate.details["Other ordinary income"] == pytest.approx(out, abs=1)


def test_tax_deferred_without_an_owner_warns():
    r = year(plan(account("ira", "tax_deferred", 100_000, owner=None)))
    assert any("no owner" in w for w in r.warnings) and r.total_gross_income == 0


def test_account_fields_survive_save_and_load():
    p = plan(account("ira", "tax_deferred", 1))
    back = Portfolio.from_dict(p.to_dict()).assets[0]
    assert (back.account_tax, back.owner_person_id) == ("tax_deferred", "ann")
