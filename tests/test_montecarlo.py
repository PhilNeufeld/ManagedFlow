"""Monte Carlo: many randomized runs of the same plan."""
import numpy as np
import pytest

import montecarlo
import simulation
from models import Cashflow, CashflowDirection, FlowType

NO_SWINGS = {k: 0.0 for k in montecarlo.DEFAULT_VOLATILITY}


def base(p):
    return p.get_scenario("base_case")


def test_with_no_randomness_every_run_equals_the_normal_simulation(sample_portfolio):
    mc = montecarlo.run_monte_carlo(sample_portfolio, base(sample_portfolio), n_runs=5, seed=1,
                                    volatility=NO_SWINGS, inflation_volatility=0.0)
    expected = [r.total_net_worth for r in simulation.run_simulation(sample_portfolio, base(sample_portfolio)).annual_results]
    for run in mc.net_worth:
        assert run == pytest.approx(expected)


def test_same_seed_same_answer_different_seed_different_answer(sample_portfolio):
    a = montecarlo.run_monte_carlo(sample_portfolio, base(sample_portfolio), n_runs=30, seed=7)
    b = montecarlo.run_monte_carlo(sample_portfolio, base(sample_portfolio), n_runs=30, seed=7)
    c = montecarlo.run_monte_carlo(sample_portfolio, base(sample_portfolio), n_runs=30, seed=8)
    assert np.array_equal(a.net_worth, b.net_worth)
    assert not np.array_equal(a.net_worth, c.net_worth)


def test_percentile_bands_are_in_order(sample_portfolio):
    mc = montecarlo.run_monte_carlo(sample_portfolio, base(sample_portfolio), n_runs=100, seed=3)
    pc = mc.percentiles()
    for low, mid, high in zip(pc[10], pc[50], pc[90]):
        assert low <= mid <= high


def test_more_volatility_means_a_wider_range(sample_portfolio):
    calm = montecarlo.run_monte_carlo(sample_portfolio, base(sample_portfolio), n_runs=200, seed=5,
                                      volatility={"equity_fund": 0.05})
    wild = montecarlo.run_monte_carlo(sample_portfolio, base(sample_portfolio), n_runs=200, seed=5,
                                      volatility={"equity_fund": 0.30})
    spread = lambda mc: mc.percentiles()[90][-1] - mc.percentiles()[10][-1]
    assert spread(wild) > spread(calm)


def test_impossible_plan_fails_every_run_in_year_one(sample_portfolio):
    start = sample_portfolio.assumptions.start_year
    sample_portfolio.add_cashflow(Cashflow(
        flow_id="huge_expense", name="Huge expense", flow_type=FlowType.LIVING_EXPENSE,
        direction=CashflowDirection.OUTFLOW, amount=50_000_000, start_year=start))
    mc = montecarlo.run_monte_carlo(sample_portfolio, base(sample_portfolio), n_runs=20, seed=1)
    assert mc.success_rate == 0
    assert mc.failure_year_counts() == {start: 20}


def test_success_rate_is_a_fraction(sample_portfolio):
    mc = montecarlo.run_monte_carlo(sample_portfolio, base(sample_portfolio), n_runs=50, seed=2)
    assert 0 <= mc.success_rate <= 1
    assert len(mc.summary_rows()) == len(mc.years)


def test_runs_must_be_positive(sample_portfolio):
    with pytest.raises(ValueError):
        montecarlo.run_monte_carlo(sample_portfolio, base(sample_portfolio), n_runs=0)
