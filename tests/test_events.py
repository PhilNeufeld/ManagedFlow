"""Events: named dates that cashflows, assets and loans hang off.

The household below retires in July 2026 (the first month of the change):
  - $120,000/yr salary ENDS at Retirement   -> runs Jan-Jun 2026
  - $48,000/yr pension STARTS at Retirement -> runs from Jul 2026
"""
import pytest

import simulation
from migrations import migrate
from models import (
    Assumptions, Cashflow, CashflowDirection, Event, FlowType, Frequency, Loan, Portfolio,
    Scenario, ValidationError,
)


def household(**event_kwargs):
    ev = dict(event_id="event_retirement", name="Retirement", year=2026, month=7)
    ev.update(event_kwargs)
    return Portfolio(
        assumptions=Assumptions(start_year=2026, end_year=2030, inflation_rate=0.0),
        events=[Event(**ev)],
        cashflows=[
            Cashflow(flow_id="salary", name="Salary", flow_type=FlowType.SALARY_INCOME,
                     direction=CashflowDirection.INFLOW, amount=120_000, start_year=2020,
                     end_event_id="event_retirement"),
            Cashflow(flow_id="pension", name="CalPERS", flow_type=FlowType.PENSION_INCOME,
                     direction=CashflowDirection.INFLOW, amount=48_000, start_year=2099,
                     start_event_id="event_retirement"),
        ],
        scenarios=[Scenario(scenario_id="base", name="Base", is_base_case=True)],
    )


def income(p, flow_id, year):
    return p.get_cashflow(flow_id).annual_amount_in_year(year)


# --- dates come from the event -----------------------------------------------------

def test_linked_dates_are_filled_in_from_the_event():
    p = household()
    salary, pension = p.get_cashflow("salary"), p.get_cashflow("pension")
    assert (salary.end_year, salary.end_month) == (2026, 6)        # stops the month before
    assert (pension.start_year, pension.start_month) == (2026, 7)  # starts that month


def test_retirement_year_is_split_not_double_counted():
    p = household()
    assert income(p, "salary", 2026) == pytest.approx(60_000)   # 6 months
    assert income(p, "pension", 2026) == pytest.approx(24_000)  # 6 months
    assert income(p, "salary", 2027) == 0
    assert income(p, "pension", 2027) == pytest.approx(48_000)


def test_january_event_ends_things_in_the_prior_december():
    p = household(year=2027, month=1)
    salary = p.get_cashflow("salary")
    assert (salary.end_year, salary.end_month) == (2026, 12)
    assert income(p, "salary", 2026) == pytest.approx(120_000)


def test_monthly_flow_starting_in_march_gets_ten_months():
    cf = Cashflow(flow_id="x", name="x", flow_type=FlowType.RENT_INCOME, direction=CashflowDirection.INFLOW,
                  amount=1_000, frequency=Frequency.MONTHLY, start_year=2026, start_month=3)
    assert cf.annual_amount_in_year(2026) == pytest.approx(10_000)
    assert cf.annual_amount_in_year(2027) == pytest.approx(12_000)


def test_whole_year_flows_are_unchanged():
    cf = Cashflow(flow_id="x", name="x", flow_type=FlowType.RENT_INCOME, direction=CashflowDirection.INFLOW,
                  amount=12_000, start_year=2026, end_year=2027)
    assert cf.annual_amount_in_year(2026) == cf.annual_amount_in_year(2027) == pytest.approx(12_000)


# --- moving events -------------------------------------------------------------------

def test_moving_the_event_moves_everything_linked():
    p = household()
    p.replace_event("event_retirement", Event(event_id="event_retirement", name="Retirement", year=2028, month=1))
    assert income(p, "salary", 2027) == pytest.approx(120_000)
    assert income(p, "pension", 2027) == 0
    assert income(p, "pension", 2028) == pytest.approx(48_000)


def test_scenario_can_move_an_event_without_changing_the_base_plan():
    p = household()
    later = Scenario(scenario_id="retire_2028", name="Retire 2028",
                     event_overrides={"event_retirement": {"year": 2028, "month": 1}})
    p.scenarios.append(later)

    base = {r.year: r for r in simulation.run_simulation(p, p.get_scenario("base")).annual_results}
    late = {r.year: r for r in simulation.run_simulation(p, later).annual_results}

    assert base[2027].total_gross_income == pytest.approx(48_000)   # retired: pension only
    assert late[2027].total_gross_income == pytest.approx(120_000)  # still working
    assert p.get_event("event_retirement").year == 2026             # stored plan untouched


def test_unknown_event_in_scenario_is_a_warning_not_a_crash():
    p = household()
    s = Scenario(scenario_id="s", name="s", event_overrides={"no_such_event": {"year": 2030}})
    result = simulation.run_simulation(p, s)
    assert any("unknown event_id" in w for w in result.annual_results[0].warnings)


# --- assets and loans ----------------------------------------------------------------------

def test_loan_paid_off_at_event():
    p = household()
    p.add_liability(Loan(liability_id="loan", name="Loan", balance=100_000, annual_interest_rate=0.06,
                         start_year=2026, term_months=360, end_event_id="event_retirement"))
    assert p.get_liability("loan").payoff_year == 2026
    years = simulation.run_simulation(p, p.get_scenario("base")).annual_results
    assert years[0].liability_results[0].paid_off_this_year
    assert years[1].total_liabilities == 0


# --- safety checks ---------------------------------------------------------------------------

def test_link_to_missing_event_is_rejected():
    p = household()
    data = p.to_dict()
    data["cashflows"][0]["end_event_id"] = "no_such_event"
    with pytest.raises(ValidationError):
        Portfolio.from_dict(data)


def test_end_before_start_is_rejected():
    with pytest.raises(ValidationError):
        Cashflow(flow_id="x", name="x", flow_type=FlowType.OTHER, direction=CashflowDirection.INFLOW,
                 amount=1, start_year=2027, start_month=6, end_year=2027, end_month=3)


def test_deleting_an_event_keeps_dates_and_unlinks():
    p = household()
    p.remove_event("event_retirement")
    salary = p.get_cashflow("salary")
    assert salary.end_event_id is None and (salary.end_year, salary.end_month) == (2026, 6)


def test_events_survive_save_and_load():
    p = household()
    assert Portfolio.from_dict(p.to_dict()).to_dict() == p.to_dict()


def test_old_files_get_an_empty_events_list(v0_1_data):
    assert migrate(v0_1_data)["events"] == []
