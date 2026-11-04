"""Quick add, tested with a FAKE Claude client: no API key, no network,
no cost. Each test hands quick_add the answer Claude might give and checks
what ManagedFlow does with it."""
from types import SimpleNamespace

import pytest

import quick_add
from models import Event

RENTAL = "real_estate_rental_1"


class FakeClient:
    """Stands in for anthropic.Anthropic(); records the request it was sent."""

    def __init__(self, tool_input=None, blocks=None):
        self.sent = None
        if blocks is None:
            blocks = [SimpleNamespace(type="tool_use", name=quick_add.TOOL_NAME, input=tool_input)]
        self._response = SimpleNamespace(content=blocks)
        self.messages = self

    def create(self, **kwargs):
        self.sent = kwargs
        return self._response


def draft(portfolio, records, questions=None, text="anything"):
    client = FakeClient({"records": records, "questions": questions or []})
    return quick_add.draft_records(text, portfolio, client=client), client


# --- the request we send --------------------------------------------------------------

def test_request_forces_the_tool_and_describes_the_portfolio(sample_portfolio):
    _, client = draft(sample_portfolio, [])
    assert client.sent["tool_choice"] == {"type": "tool", "name": quick_add.TOOL_NAME}
    assert client.sent["tools"][0]["name"] == quick_add.TOOL_NAME
    context = client.sent["messages"][0]["content"]
    assert f"{RENTAL}: Sample Rental Property" in context


def test_context_shares_names_not_dollar_amounts(sample_portfolio):
    _, client = draft(sample_portfolio, [])
    context = client.sent["messages"][0]["content"]
    rental_value = sample_portfolio.get_asset(RENTAL).initial_value
    assert f"{rental_value:g}" not in context and "$" not in context


def test_empty_text_never_calls_the_api(sample_portfolio):
    client = FakeClient({"records": []})
    result = quick_add.draft_records("   ", sample_portfolio, client=client)
    assert client.sent is None and result.drafts == []


# --- turning answers into records ---------------------------------------------------------

def test_house_loan_and_rent_in_one_sentence_link_together(sample_portfolio):
    result, _ = draft(sample_portfolio, [
        {"record_type": "asset", "ref": "r1", "name": "4392 Brix", "asset_type": "real_estate",
         "initial_value": 295_000, "annual_growth_rate": 0.03},
        {"record_type": "loan", "ref": "r2", "name": "Brix mortgage", "lender": "Nation Star",
         "balance": 194_613, "annual_interest_rate": 0.0425, "term_months": 300, "secured_by_asset_id": "r1"},
        {"record_type": "cashflow", "name": "Brix rent", "flow_type": "rent_income", "direction": "inflow",
         "amount": 1_150, "frequency": "monthly", "asset_id": "r1", "taxable": True},
    ])
    assert all(d.ok for d in result.drafts), [d.errors for d in result.drafts]
    house, loan, rent = (d.record for d in result.drafts)
    assert loan.secured_by_asset_id == house.asset_id     # "r1" became the real id
    assert rent.asset_id == house.asset_id

    quick_add.add_drafts(sample_portfolio, result.drafts)
    assert sample_portfolio.get_liability(loan.liability_id).secured_by_asset_id == house.asset_id
    assert rent in sample_portfolio.cashflows_for_asset(house.asset_id)


def test_cashflow_linked_to_an_existing_event_takes_its_date(sample_portfolio):
    sample_portfolio.add_event(Event(event_id="event_deb_retires", name="Debbie retires", year=2030, month=7))
    result, _ = draft(sample_portfolio, [
        {"record_type": "cashflow", "name": "Debbie CalPERS", "flow_type": "pension_income",
         "direction": "inflow", "amount": 1_700, "frequency": "monthly",
         "start_event_id": "event_deb_retires", "annual_growth_rate": 0.02, "taxable": True},
    ])
    quick_add.add_drafts(sample_portfolio, result.drafts)
    pension = result.drafts[0].record
    assert (pension.start_year, pension.start_month) == (2030, 7)
    assert pension.annual_amount_in_year(2030) == pytest.approx(1_700 * 6)


def test_rate_written_as_a_percent_is_flagged_not_guessed(sample_portfolio):
    result, _ = draft(sample_portfolio, [
        {"record_type": "loan", "name": "Bad rate", "balance": 10_000, "annual_interest_rate": 6, "term_months": 60},
    ])
    assert not result.drafts[0].ok
    assert "fraction" in result.drafts[0].errors[0]
    assert quick_add.add_drafts(sample_portfolio, result.drafts) == []  # nothing added


def test_link_to_something_that_doesnt_exist_is_flagged(sample_portfolio):
    result, _ = draft(sample_portfolio, [
        {"record_type": "cashflow", "name": "Rent", "amount": 100, "asset_id": "no_such_house"},
    ])
    assert any("doesn't match" in e for e in result.drafts[0].errors)


def test_invalid_field_value_is_reported_per_record(sample_portfolio):
    result, _ = draft(sample_portfolio, [
        {"record_type": "cashflow", "name": "Weird", "amount": 100, "flow_type": "not_a_real_type"},
        {"record_type": "event", "name": "Fine", "year": 2027, "month": 3},
    ])
    bad, good = result.drafts
    assert not bad.ok and good.ok


def test_ids_are_generated_by_the_app_and_never_collide(sample_portfolio):
    existing = sample_portfolio.cashflows[0]
    result, _ = draft(sample_portfolio, [
        {"record_type": "cashflow", "name": existing.name, "amount": 1},
        {"record_type": "cashflow", "name": existing.name, "amount": 2},
    ])
    ids = {d.record.flow_id for d in result.drafts}
    assert len(ids) == 2 and existing.flow_id not in ids


def test_questions_and_assumptions_are_passed_through(sample_portfolio):
    result, _ = draft(sample_portfolio,
                      [{"record_type": "event", "name": "Retirement", "year": 2026, "month": 7,
                        "assumptions": ["Assumed July since you said 'mid-2026'"]}],
                      questions=["Which pension plan?"])
    assert result.questions == ["Which pension plan?"]
    assert result.drafts[0].assumptions == ["Assumed July since you said 'mid-2026'"]


def test_other_blocks_before_the_tool_call_are_skipped(sample_portfolio):
    blocks = [
        SimpleNamespace(type="thinking", name=None, input=None),
        SimpleNamespace(type="tool_use", name=quick_add.TOOL_NAME,
                        input={"records": [{"record_type": "event", "name": "E", "year": 2027}]}),
    ]
    result = quick_add.draft_records("x", sample_portfolio, client=FakeClient(blocks=blocks))
    assert result.drafts[0].ok


def test_no_tool_call_comes_back_as_a_question(sample_portfolio):
    client = FakeClient(blocks=[SimpleNamespace(type="text", name=None, input=None)])
    result = quick_add.draft_records("x", sample_portfolio, client=client)
    assert result.drafts == [] and result.questions
