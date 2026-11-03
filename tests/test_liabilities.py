"""Loans and credit lines, checked against answers you can verify by hand
or with any online mortgage calculator."""
import pytest

import simulation
from migrations import migrate
from models import CreditLine, Loan, Portfolio, ValidationError

RENTAL = "real_estate_rental_1"


def make_loan(**overrides):
    fields = dict(
        liability_id="test_loan", name="Test mortgage", balance=100_000,
        annual_interest_rate=0.06, start_year=2026, term_months=360,
    )
    fields.update(overrides)
    return Loan(**fields)


# --- the math -----------------------------------------------------------------

def test_payment_matches_mortgage_calculator():
    # $100,000, 6%, 30 years -> $599.55/month (any mortgage calculator)
    assert make_loan().scheduled_monthly_payment() == pytest.approx(599.55, abs=0.005)


def test_balance_after_one_year_matches_closed_form():
    loan = make_loan()
    r, P, B = 0.06 / 12, loan.scheduled_monthly_payment(), 100_000
    # Textbook balance after k payments: B(1+r)^k - P((1+r)^k - 1)/r
    expected = B * (1 + r) ** 12 - P * ((1 + r) ** 12 - 1) / r

    interest, principal, ending = loan.year_activity(B)

    assert ending == pytest.approx(expected, abs=0.01)
    assert interest + principal == pytest.approx(12 * P, abs=0.01)  # 12 payments made


def test_loan_pays_off_on_schedule():
    loan = make_loan()
    balance, total_interest = 100_000.0, 0.0
    for _year in range(30):
        interest, _principal, balance = loan.year_activity(balance)
        total_interest += interest
    assert balance == pytest.approx(0, abs=0.01)
    # Total interest = all payments minus the amount borrowed
    assert total_interest == pytest.approx(360 * loan.scheduled_monthly_payment() - 100_000, abs=0.05)


def test_zero_interest_loan():
    loan = make_loan(annual_interest_rate=0.0, term_months=12, balance=12_000)
    assert loan.year_activity(12_000) == pytest.approx((0.0, 12_000, 0.0))


def test_statement_payment_overrides_term():
    loan = make_loan(term_months=None, monthly_payment=1_000)
    assert loan.scheduled_monthly_payment() == 1_000


def test_payment_too_small_to_cover_interest_is_rejected():
    with pytest.raises(ValidationError):
        make_loan(term_months=None, monthly_payment=400)  # interest alone is $500/month


def test_credit_line_is_interest_only():
    heloc = CreditLine(liability_id="heloc", name="HELOC", balance=60_000,
                       annual_interest_rate=0.08, start_year=2026, credit_limit=100_000)
    assert heloc.year_activity(60_000) == pytest.approx((4_800, 0, 60_000))


def test_credit_line_over_limit_is_rejected():
    with pytest.raises(ValidationError):
        CreditLine(liability_id="x", name="x", balance=120_000, annual_interest_rate=0.08,
                   start_year=2026, credit_limit=100_000)


# --- saving and loading -----------------------------------------------------------

def test_loans_and_credit_lines_survive_save_and_load(sample_portfolio):
    sample_portfolio.add_liability(make_loan(secured_by_asset_id=RENTAL))
    sample_portfolio.add_liability(CreditLine(liability_id="heloc", name="HELOC", balance=5_000,
                                              annual_interest_rate=0.08, start_year=2026))
    reloaded = Portfolio.from_dict(sample_portfolio.to_dict())
    assert isinstance(reloaded.get_liability("test_loan"), Loan)
    assert isinstance(reloaded.get_liability("heloc"), CreditLine)
    assert reloaded.to_dict() == sample_portfolio.to_dict()


def test_old_files_get_an_empty_liabilities_list(v0_1_data):
    new = migrate(v0_1_data)
    assert new["liabilities"] == []
    assert all(b["liability_ids"] == [] for b in new["bundles"])


def test_loan_must_point_at_a_real_asset(sample_portfolio):
    with pytest.raises(ValidationError):
        sample_portfolio.add_liability(make_loan(secured_by_asset_id="no_such_asset"))


def test_deleting_the_house_keeps_the_loan(sample_portfolio):
    sample_portfolio.add_liability(make_loan(secured_by_asset_id=RENTAL))
    sample_portfolio.remove_asset(RENTAL)
    loan = sample_portfolio.get_liability("test_loan")
    assert loan is not None and loan.secured_by_asset_id is None


# --- in the simulation ---------------------------------------------------------------

def _base_case(portfolio):
    return simulation.run_simulation(portfolio, portfolio.get_scenario("base_case"))


def test_net_worth_subtracts_debt_and_expenses_include_payments(sample_portfolio):
    start = sample_portfolio.assumptions.start_year
    without = _base_case(sample_portfolio).annual_results[0]

    sample_portfolio.add_liability(make_loan(start_year=start, secured_by_asset_id=RENTAL))
    with_loan = _base_case(sample_portfolio).annual_results[0]

    loan_year = with_loan.liability_results[0]
    assert loan_year.debt_service == pytest.approx(12 * 599.55, abs=0.1)
    assert with_loan.total_expenses == pytest.approx(without.total_expenses + loan_year.debt_service)
    assert with_loan.total_liabilities == pytest.approx(loan_year.ending_balance)
    assert with_loan.total_net_worth == pytest.approx(with_loan.total_assets - with_loan.total_liabilities)


def test_future_loan_owes_nothing_until_it_starts(sample_portfolio):
    start = sample_portfolio.assumptions.start_year
    sample_portfolio.add_liability(make_loan(start_year=start + 2))
    years = _base_case(sample_portfolio).annual_results
    assert years[0].total_liabilities == 0 and years[1].total_liabilities == 0
    assert years[2].total_liabilities > 0


def test_selling_the_house_pays_off_its_loan(sample_portfolio):
    scenario = sample_portfolio.get_scenario("sell_rental_2032")
    sale_year = scenario.asset_overrides[RENTAL]["sale_year"]
    sample_portfolio.add_liability(make_loan(
        start_year=sample_portfolio.assumptions.start_year, secured_by_asset_id=RENTAL))

    years = {r.year: r for r in simulation.run_simulation(sample_portfolio, scenario).annual_results}

    sale = years[sale_year].liability_results[0]
    assert sale.paid_off_this_year and sale.ending_balance == 0
    assert sale.principal_paid > sale.beginning_balance * 0.9  # the lump-sum payoff
    assert years[sale_year + 1].total_liabilities == 0


def test_mortgage_travels_with_its_house_into_scoped_runs(sample_portfolio):
    sample_portfolio.add_liability(make_loan(
        start_year=sample_portfolio.assumptions.start_year, secured_by_asset_id=RENTAL))

    llc = simulation.run_simulation(sample_portfolio, sample_portfolio.get_scenario("rental_llc_only"))
    salary = simulation.run_simulation(sample_portfolio, sample_portfolio.get_scenario("spouse_a_earnings_only"))

    assert llc.annual_results[0].total_liabilities > 0     # rental LLC scope includes the house
    assert salary.annual_results[0].total_liabilities == 0  # salary-only scope doesn't
