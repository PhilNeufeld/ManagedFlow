"""Receivables: money owed TO you, paid down over time (a loan in reverse).

Example (hand-checkable): a $1,200,000 fee owed to you, 7.75%, 180 monthly payments
starting Jul 2027 -> payment = 1,200,000 x r / (1 - (1+r)^-180), r = 0.0775/12 = $11,295.31.
2027 gets 6 payments = $67,771.86, split into interest and principal; paid off Jun 2042.
"""
import pytest

import explorer
import simulation
from models import (
    Asset, AssetType, Assumptions, Cashflow, CashflowDirection, Entity, Event, FlowType, Ownership, Person,
    Portfolio, Scenario, ValidationError, amortized_monthly_payment,
)


def fee(**kw):
    terms = dict(asset_id="fee", name="Developer fee owed", asset_type=AssetType.RECEIVABLE, owner="Us",
                 initial_value=1_200_000, start_year=2026, receivable_rate=0.0775, receivable_months=180,
                 first_payment_year=2027, first_payment_month=7, principal_taxable=True,
                 principal_tax_category="self_employment_income", earner_person_id="alex")
    terms.update(kw)
    return Asset(**terms)


def plan(*assets, **kw):
    return Portfolio(
        assumptions=Assumptions(start_year=2026, end_year=2043, inflation_rate=0.0, tax_method="detailed"),
        people=[Person(person_id="alex", name="Alex", birth_year=1975)],
        assets=list(assets), scenarios=[Scenario(scenario_id="base", name="Base", is_base_case=True)], **kw)


def years(p):
    return {r.year: r for r in simulation.run_simulation(p, p.get_scenario("base")).annual_results}


def test_payment_and_schedule_match_the_amortization():
    a = fee()
    assert a.receivable_monthly_payment() == pytest.approx(11_295.31, abs=0.01)
    s = a.receivable_schedule(2026, 2043)
    assert s[2026] == (0.0, 0.0, 1_200_000)                       # flat until payments start
    i, p, bal = s[2027]
    assert i + p == pytest.approx(6 * 11_295.31, abs=0.05)
    assert bal == pytest.approx(1_200_000 - p)
    assert i > p                                                   # early payments are mostly interest
    assert s[2042][2] == 0 and s[2041][2] > 0 and s[2043] == (0.0, 0.0, 0.0)   # last payment Jun 2042


def test_first_month_interest_is_balance_times_rate_over_12():
    s = fee(first_payment_month=12).receivable_schedule(2027, 2027)
    assert s[2027][0] == pytest.approx(1_200_000 * 0.0775 / 12)


def test_simulation_counts_cash_balance_and_taxes_each_part():
    p = plan(fee())
    y = years(p)
    i, pr, bal = fee().receivable_schedule(2026, 2027)[2027]
    assert y[2027].total_gross_income == pytest.approx(i + pr)
    assert y[2026].total_net_worth >= 1_200_000 - 1                 # the balance counts in net worth
    d = y[2027].tax_estimate.details
    assert d["Other ordinary income"] == pytest.approx(i)           # interest
    assert d["Self-employment income"] == pytest.approx(pr)        # principal, as the fee it pays
    assert d["Self-employment tax - Alex"] > 0


def test_principal_on_a_plain_note_is_not_taxed():
    p = plan(fee(principal_taxable=False, principal_tax_category="none"))
    d = years(p)[2027].tax_estimate.details
    assert d["Self-employment income"] == 0


def test_your_share_through_an_entity():
    p = plan(fee(entity_id="llc"),
             entities=[Entity(entity_id="us", name="Us", entity_type="joint"),
                       Entity(entity_id="llc", name="LLC", entity_type="llc")],
             ownerships=[Ownership(owner_entity_id="us", owned_type="entity", owned_id="llc", percent=0.5)])
    i, pr, _ = fee().receivable_schedule(2026, 2027)[2027]
    assert years(p)[2027].total_gross_income == pytest.approx((i + pr) * 0.5)


def test_payments_can_start_at_an_event():
    p = plan(fee(first_payment_year=None, first_payment_event_id="close"),
             events=[Event(event_id="close", name="Perm close", year=2028, month=1)])
    p.apply_event_dates()
    y = years(p)
    assert y[2027].total_gross_income == 0 and y[2028].total_gross_income == pytest.approx(12 * 11_295.31, abs=0.1)


def test_receivable_needs_terms_and_is_never_drawn_down():
    with pytest.raises(ValidationError):
        Asset(asset_id="x", name="x", asset_type="receivable", owner="u", initial_value=1, start_year=2026)
    spend = Cashflow(flow_id="spend", name="Spending", flow_type=FlowType.LIVING_EXPENSE,
                     direction=CashflowDirection.OUTFLOW, amount=500_000, start_year=2026)
    p = plan(fee(), cashflows=[spend])
    y = years(p)
    assert y[2026].withdrawal_amount == 0                           # a shortfall can't be paid from it
    assert next(a for a in y[2026].asset_results if a.asset_id == "fee").ending_value == pytest.approx(1_200_000)


def test_explore_shows_interest_and_principal_under_the_receivable():
    p = plan(fee())
    res = explorer.run(p, p.get_scenario("base"))
    nodes = explorer.build_tree(res.working_portfolio)
    node = next(n for n in nodes.values() if n.ref_id == "fee")
    assert sorted(nodes[c].label for c in node.children) == ["Developer fee owed - interest received",
                                                             "Developer fee owed - principal received"]
    whole = explorer.node_series(nodes, explorer.ROOT, res)[1]
    assert whole["Net cash flow"] == pytest.approx(res.annual_results[1].net_cashflow)


def test_terms_survive_save_and_load():
    a = fee(first_payment_event_id=None)
    back = Asset.from_dict(a.to_dict())
    assert (back.receivable_rate, back.receivable_months, back.principal_tax_category.value) == \
        (0.0775, 180, "self_employment_income")


def test_renumbering_keys_keeps_the_first_payment_event_link():
    import storage
    p = plan(fee(first_payment_year=None, first_payment_event_id="close"),
             events=[Event(event_id="close", name="Perm close", year=2028, month=1)])
    q = storage.with_integer_keys(p)
    assert q.assets[0].first_payment_event_id == q.events[0].event_id == 1
