"""The fictional Alex & Sam household (example_data.py), checked against
arithmetic you can do on paper. Open data/example_transition.json in the app
to see the same numbers."""
import pytest

import example_data
import montecarlo
import simulation
from models import Portfolio, amortized_monthly_payment


@pytest.fixture
def example():
    return example_data.build_example_portfolio()


def run(p, scenario_id):
    return {r.year: r for r in simulation.run_simulation(p, p.get_scenario(scenario_id)).annual_results}


def test_retirement_year_income_adds_up(example):
    # 60,000 (6 mo salary) + 24,000 (6 mo pension) + 70,000 (Sam) + 26,400 (12 x 2,200 rent)
    assert run(example, "base")[2026].total_gross_income == pytest.approx(180_400)


def test_mortgage_payment_matches_a_calculator():
    assert amortized_monthly_payment(180_000, 0.045, 240) == pytest.approx(1_138.77, abs=0.01)


def test_first_year_debt_service(example):
    year = run(example, "base")[2026]
    mortgage = 12 * amortized_monthly_payment(180_000, 0.045, 240)
    heloc_interest = 20_000 * 0.08
    assert year.total_debt_service == pytest.approx(mortgage + heloc_interest, abs=0.05)


def test_selling_the_rental_pays_off_its_mortgage_and_stops_the_rent(example):
    years = run(example, "base")
    assert years[2033].total_liabilities > 20_000           # mortgage + HELOC
    assert years[2034].total_liabilities == pytest.approx(20_000)  # HELOC only
    rent = example.get_cashflow("flow_elm_rent")
    assert rent.annual_amount_in_year(2033) > 0 and rent.annual_amount_in_year(2034) == 0


def test_retiring_later_means_more_income_before_2028(example):
    base, late = run(example, "base"), run(example, "alex_retires_2028")
    # Retiring later in 2027: full $120,000 salary instead of the pension.
    # In the base plan 2027 is the pension's 2nd year, so it has had one 2% COLA:
    pension_2027 = 48_000 * 1.02  # = 48,960 (an earlier version of this test forgot the COLA)
    assert late[2027].total_gross_income - base[2027].total_gross_income == pytest.approx(120_000 - pension_2027)


def test_higher_inflation_is_worse(example):
    assert run(example, "higher_inflation")[2050].total_net_worth < run(example, "base")[2050].total_net_worth


def test_example_file_round_trips(example):
    assert Portfolio.from_dict(example.to_dict()).to_dict() == example.to_dict()


def test_monte_carlo_on_the_example_is_reproducible(example):
    a = montecarlo.run_monte_carlo(example, example.get_scenario("base"), n_runs=100, seed=42)
    b = montecarlo.run_monte_carlo(example, example.get_scenario("base"), n_runs=100, seed=42)
    assert a.success_rate == b.success_rate
    assert 0 <= a.success_rate <= 1
