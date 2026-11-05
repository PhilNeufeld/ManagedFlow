"""People: birthdays drive age-based events, ages appear in results, and a
planning "death" event makes survivor scenarios possible."""
import pytest

import example_data
import simulation
from migrations import migrate
from models import (
    Assumptions, Cashflow, CashflowDirection, Event, FlowType, Person, Portfolio, Scenario, ValidationError,
)


@pytest.fixture
def example():
    return example_data.build_example_portfolio()


def by_year(p, scenario):
    return {r.year: r for r in simulation.run_simulation(p, scenario).annual_results}


# --- the Person class -----------------------------------------------------------------

def test_age_and_birthday_math():
    sam = Person(person_id="p", name="Sam", birth_year=1967, birth_month=7)
    assert sam.age_in(2030) == 63
    assert sam.date_at_age(65) == (2032, 7)
    assert sam.final_year == 1967 + 92


def test_bad_birthday_is_rejected():
    with pytest.raises(ValidationError):
        Person(person_id="p", name="X", birth_year=1967, birth_month=13)


# --- age-based events -------------------------------------------------------------------

def test_age_based_events_land_on_the_birthday(example):
    assert (example.get_event("event_alex_retires").year, example.get_event("event_alex_retires").month) == (2026, 7)
    assert (example.get_event("event_sam_retires").year, example.get_event("event_sam_retires").month) == (2030, 7)


def test_changing_a_birthday_moves_their_events_and_linked_records(example):
    sam = example.get_person("person_sam")
    example.replace_person("person_sam", Person(person_id="person_sam", name="Sam", birth_year=1967, birth_month=1))
    assert (example.get_event("event_sam_retires").year, example.get_event("event_sam_retires").month) == (2030, 1)
    salary = example.get_cashflow("flow_sam_salary")
    assert (salary.end_year, salary.end_month) == (2029, 12)  # stops the month before
    assert sam.birth_month == 7  # the old object is untouched; the portfolio holds the new one


def test_scenario_can_change_the_age(example):
    # (Sam's Social Security claim moved to 67 in both, so the earnings test doesn't enter into it)
    claims_67 = {"event_sam_ss": {"age": 67}}
    base = by_year(example, Scenario(scenario_id="b", name="b", event_overrides=claims_67))
    later = by_year(example, Scenario(scenario_id="l", name="l",
                                      event_overrides={**claims_67, "event_sam_retires": {"age": 65}}))
    # 2031: base has Sam's pension in its 2nd year (20,000 x 1.02 = 20,400);
    # retiring at 65 (Jul 2032) means a full 70,000 salary instead.
    assert later[2031].total_gross_income - base[2031].total_gross_income == pytest.approx(70_000 - 20_400)
    assert example.get_event("event_sam_retires").age == 63  # stored plan untouched


def test_fixed_year_override_on_an_age_event_wins(example):
    s = Scenario(scenario_id="s", name="s", event_overrides={"event_sam_retires": {"year": 2027, "month": 1}})
    years = by_year(example, s)
    assert years[2027].ages["Sam"] == 60
    sam_salary = example.get_cashflow("flow_sam_salary")
    assert sam_salary.annual_amount_in_year(2027) == pytest.approx(70_000)  # stored plan: still working
    # ...but in the scenario, 2027 income has no Sam salary:
    assert years[2027].total_gross_income < by_year(example, example.get_scenario("base"))[2027].total_gross_income


def test_age_override_on_a_fixed_date_event_warns():
    p = Portfolio(events=[Event(event_id="e", name="E", year=2030)])
    s = Scenario(scenario_id="s", name="s", event_overrides={"e": {"age": 70}})
    first_year = simulation.run_simulation(p, s).annual_results[0]
    assert any("isn't tied to a person" in w for w in first_year.warnings)


def test_age_event_needs_both_person_and_age():
    with pytest.raises(ValidationError):
        Event(event_id="e", name="E", year=2030, person_id="p")


def test_event_tied_to_missing_person_is_rejected(example):
    data = example.to_dict()
    data["people"] = []
    with pytest.raises(ValidationError):
        Portfolio.from_dict(data)


def test_removing_a_person_keeps_their_event_dates(example):
    example.remove_person("person_sam")
    ev = example.get_event("event_sam_retires")
    assert ev.person_id is None and (ev.year, ev.month) == (2030, 7)


# --- results -------------------------------------------------------------------------------

def test_results_show_ages(example):
    years = by_year(example, example.get_scenario("base"))
    assert years[2026].ages == {"Alex": 61, "Sam": 59}
    assert years[2050].ages == {"Alex": 85, "Sam": 83}


def test_plan_that_ends_too_early_gets_a_warning(example):
    first = by_year(example, example.get_scenario("base"))[2026]
    assert any("Consider extending the end year" in w for w in first.warnings)


# --- survivor scenario ---------------------------------------------------------------------

def survivor_household():
    """Pat (born Jan 1960) has a $40,000 pension with a 50% survivor benefit
    for Lee. A planning 'death' event at age 92 ends one and starts the other."""
    return Portfolio(
        assumptions=Assumptions(start_year=2026, end_year=2060, inflation_rate=0.0),
        people=[Person(person_id="pat", name="Pat", birth_year=1960, birth_month=1),
                Person(person_id="lee", name="Lee", birth_year=1962, birth_month=1)],
        events=[Event(event_id="pat_death", name="Pat's death (planning)", year=0, person_id="pat", age=92)],
        cashflows=[
            Cashflow(flow_id="pension", name="Pat pension", flow_type=FlowType.PENSION_INCOME,
                     direction=CashflowDirection.INFLOW, amount=40_000, start_year=2026, end_event_id="pat_death"),
            Cashflow(flow_id="survivor", name="Survivor benefit (50%)", flow_type=FlowType.PENSION_INCOME,
                     direction=CashflowDirection.INFLOW, amount=20_000, start_year=2099, start_event_id="pat_death"),
        ],
        scenarios=[Scenario(scenario_id="base", name="Base"),
                   Scenario(scenario_id="pat_dies_75", name="Pat dies at 75",
                            event_overrides={"pat_death": {"age": 75}})],
    )


def test_survivor_scenario_switches_to_the_survivor_benefit():
    p = survivor_household()
    base = by_year(p, p.get_scenario("base"))
    early = by_year(p, p.get_scenario("pat_dies_75"))
    assert base[2040].total_gross_income == pytest.approx(40_000)   # Pat alive at 80
    assert early[2034].total_gross_income == pytest.approx(40_000)  # Pat 74
    assert early[2035].total_gross_income == pytest.approx(20_000)  # Pat would be 75: survivor only
    assert early[2040].total_gross_income == pytest.approx(20_000)


# --- saving and loading ------------------------------------------------------------------------

def test_people_survive_save_and_load(example):
    assert Portfolio.from_dict(example.to_dict()).to_dict() == example.to_dict()


def test_old_files_get_an_empty_people_list(v0_1_data):
    assert migrate(v0_1_data)["people"] == []

