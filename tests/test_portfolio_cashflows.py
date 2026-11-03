"""The Portfolio methods that manage the single cashflow list."""
import pytest

from models import Cashflow, CashflowDirection, FlowType, ValidationError


def _rent(flow_id="test_rent", asset_id=None):
    return Cashflow(
        flow_id=flow_id, name="Test rent", flow_type=FlowType.RENT_INCOME,
        direction=CashflowDirection.INFLOW, amount=1000, start_year=2026, asset_id=asset_id,
    )


def test_cashflow_must_point_at_a_real_asset(sample_portfolio):
    with pytest.raises(ValidationError):
        sample_portfolio.add_cashflow(_rent(asset_id="no_such_asset"))


def test_duplicate_flow_id_is_rejected(sample_portfolio):
    existing_id = sample_portfolio.cashflows[0].flow_id
    with pytest.raises(ValidationError):
        sample_portfolio.add_cashflow(_rent(flow_id=existing_id))


def test_removing_an_asset_removes_its_cashflows(sample_portfolio):
    asset_id = "real_estate_rental_1"
    linked = sample_portfolio.cashflows_for_asset(asset_id)
    assert linked, "sample rental should have cashflows"
    total_before = len(sample_portfolio.cashflows)

    removed = sample_portfolio.remove_asset(asset_id)

    assert len(removed) == len(linked)
    assert len(sample_portfolio.cashflows) == total_before - len(linked)
    assert sample_portfolio.cashflows_for_asset(asset_id) == []


def test_relinking_a_cashflow_is_just_a_new_asset_id(sample_portfolio):
    sample_portfolio.add_cashflow(_rent())  # household-level
    assert sample_portfolio.get_cashflow("test_rent") in sample_portfolio.household_level_cashflows()

    sample_portfolio.replace_cashflow("test_rent", _rent(asset_id="real_estate_rental_1"))

    assert sample_portfolio.get_cashflow("test_rent") in sample_portfolio.cashflows_for_asset("real_estate_rental_1")
