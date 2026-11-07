"""Spending policy (guardrails) and highest steady spending."""
import dataclasses

import pytest

import montecarlo
import simulation
import spending
from models import (Actual, Asset, Assumptions, Cashflow, CashflowDirection, FlowType, LiquidityCategory,
                    Portfolio, Scenario, TaxCategory, ValidationError)


def retiree(spend=60_000, income=20_000, savings=600_000, years=20, growth=0.0, **scenario):
    """Income below spending, so savings fund the gap -- a draw rate guardrails can react to."""
    return Portfolio(
        assumptions=Assumptions(start_year=2026, end_year=2025 + years, inflation_rate=0.0, tax_method="flat",
                                default_tax_rates={"ordinary_income": 0.0}),
        assets=[Asset(asset_id="savings", name="Savings", asset_type="cash", owner="", initial_value=savings,
                      start_year=2026, annual_growth_rate=growth, liquidity_category=LiquidityCategory.LIQUID)],
        cashflows=[
            Cashflow(flow_id="pension", name="Pension", flow_type=FlowType.PENSION_INCOME,
                     direction=CashflowDirection.INFLOW, amount=income, start_year=2026,
                     tax_category=TaxCategory.ORDINARY_INCOME),
            Cashflow(flow_id="living", name="Living expenses", flow_type=FlowType.LIVING_EXPENSE,
                     direction=CashflowDirection.OUTFLOW, amount=spend, start_year=2026, taxable=False),
        ],
        scenarios=[Scenario(scenario_id="base", name="Base", is_base_case=True, **scenario)],
    )


def run(p):
    return simulation.run_simulation(p, p.scenarios[0]).annual_results


# --- policy -------------------------------------------------------------------------------------

def test_fixed_policy_spends_the_plan():
    res = run(retiree())
    assert all(r.spending_multiplier == 1.0 for r in res)
    assert all(r.living_expenses == pytest.approx(60_000) for r in res)


def test_guardrails_cut_when_the_draw_rate_is_high():
    # $40K/yr gap on $600K = 6.7% draw > 6% upper guardrail -> cut 10% the next year
    res = run(retiree(spending_policy="guardrails"))
    assert res[0].spending_multiplier == 1.0
    assert res[1].spending_multiplier == pytest.approx(0.9)
    assert "cut 10%" in res[1].spending_action
    assert res[1].living_expenses == pytest.approx(54_000)


def test_guardrails_never_go_below_the_floor():
    res = run(retiree(spend=100_000, spending_policy="guardrails", spending_floor=0.8))
    assert min(r.spending_multiplier for r in res) == pytest.approx(0.8)


def test_guardrails_restore_cuts_when_the_draw_rate_falls():
    # Big income later: the draw rate turns negative, so cuts are restored, but not above the plan (ceiling 100%)
    p = retiree(spending_policy="guardrails")
    p.cashflows.append(Cashflow(flow_id="windfall", name="Windfall", flow_type=FlowType.OTHER,
                                direction=CashflowDirection.INFLOW, amount=100_000, start_year=2030, taxable=False))
    res = run(p)
    assert min(r.spending_multiplier for r in res) < 1.0
    assert res[-1].spending_multiplier == pytest.approx(1.0)
    assert max(r.spending_multiplier for r in res) <= 1.0 + 1e-9


def test_a_ceiling_above_100_percent_allows_raises():
    res = run(retiree(income=90_000, spending_policy="guardrails", spending_ceiling=1.2))
    assert res[-1].spending_multiplier == pytest.approx(1.2)


def test_no_inflation_raise_after_a_losing_year():
    p = retiree(growth=-0.10, spending_policy="guardrails", guardrail_upper=0.5, guardrail_lower=0.0)
    p.assumptions.inflation_rate = 0.03
    p.cashflows[1].inflation_adjusted = True
    res = run(p)
    assert res[1].spending_multiplier == pytest.approx(1 / 1.03)
    assert "no inflation raise" in res[1].spending_action


def test_recorded_actual_spending_is_never_adjusted():
    p = retiree(spending_policy="guardrails")
    p.actuals.append(Actual(actual_id=1, year=2027, amount=61_000, name="Living expenses", kind="actual",
                            direction="outflow", category="living_expense", flow_id="living"))
    res = run(p)
    assert next(r for r in res if r.year == 2027).living_expenses == pytest.approx(61_000)


def test_policy_settings_are_validated():
    with pytest.raises(ValidationError):
        Scenario(scenario_id="x", name="x", spending_policy="whatever")
    with pytest.raises(ValidationError):
        Scenario(scenario_id="x", name="x", guardrail_lower=0.07, guardrail_upper=0.05)


def test_monte_carlo_reports_spending_when_it_flexes():
    p = retiree(spending_policy="guardrails", growth=0.04)
    mc = montecarlo.run_monte_carlo(p, p.scenarios[0], n_runs=30, seed=1, volatility={"cash": 0.15})
    assert mc.spending_varies and 0 < mc.share_of_runs_with_a_cut() <= 1
    assert "Spending, median" in mc.summary_rows()[0]


# --- highest steady spending ------------------------------------------------------------------------

def test_highest_steady_spending_spends_savings_down_exactly():
    # $600K over 20 years at 0% growth, $20K income -> $50K/yr is the most that lasts
    res = spending.highest_steady_spending(retiree(), retiree().scenarios[0], tolerance=50)
    assert res.amount == pytest.approx(50_000, abs=100)
    assert res.binding_year is not None and res.planned == pytest.approx(60_000)
    assert res.headroom == pytest.approx(-10_000, abs=100)


def test_a_cash_reserve_lowers_the_answer():
    plain = spending.highest_steady_spending(retiree(), retiree().scenarios[0], tolerance=50)
    reserve = spending.highest_steady_spending(retiree(), retiree().scenarios[0], min_liquid_today=100_000, tolerance=50)
    assert reserve.amount == pytest.approx(45_000, abs=100)      # ($600K - $100K) / 20 + $20K
    assert reserve.amount < plain.amount
    assert "reserve" in reserve.reason


def test_the_answer_lasts_and_a_bit_more_does_not():
    p = retiree()
    res = spending.highest_steady_spending(p, p.scenarios[0], tolerance=50)
    ok = run(spending._scaled(p, res.amount / 60_000))
    over = run(spending._scaled(p, (res.amount + 1_000) / 60_000))
    short = lambda rs: any(-r.surplus_or_deficit > r.withdrawal_amount + 1e-6 for r in rs)
    assert not short(ok) and short(over)


def test_monte_carlo_version_is_more_conservative_with_volatile_markets():
    p = retiree(growth=0.05)
    det = spending.highest_steady_spending(p, p.scenarios[0], tolerance=500)
    import montecarlo as mcmod
    old = dict(mcmod.DEFAULT_VOLATILITY)
    mcmod.DEFAULT_VOLATILITY["cash"] = 0.15
    try:
        mc = spending.highest_steady_spending(p, p.scenarios[0], tolerance=500, monte_carlo_runs=40,
                                              target_success=0.9, seed=7)
    finally:
        mcmod.DEFAULT_VOLATILITY.clear(); mcmod.DEFAULT_VOLATILITY.update(old)
    assert mc.amount < det.amount and mc.success_rate >= 0.9
