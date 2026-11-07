"""Assets in another currency (e.g. a Canadian bank account) and interest taxed as it accrues."""
import pytest

import simulation
from models import Asset, AssetType, Assumptions, LiquidityCategory, Portfolio, ValidationError
from scenarios import apply_scenario


def _cad_account(**overrides):
    fields = dict(asset_id="cad", name="Canadian savings", asset_type=AssetType.CASH, owner="Household",
                  initial_value=100_000, start_year=2026, annual_growth_rate=0.04, currency="cad",
                  liquidity_category=LiquidityCategory.SEMI_LIQUID, account_tax="taxable")
    fields.update(overrides)
    return Asset(**fields)


def _with_account(portfolio, rate=0.72, **overrides):
    portfolio.assumptions.fx_rates = {"CAD": rate} if rate else {}
    portfolio.add_asset(_cad_account(**overrides))
    return portfolio


def _base(portfolio):
    return next(s for s in portfolio.scenarios if s.is_base_case)


def test_currency_code_is_normalized_and_checked():
    assert _cad_account().currency == "CAD"
    with pytest.raises(ValidationError):
        _cad_account(currency="Canadian")


def test_exchange_rate_must_be_positive():
    with pytest.raises(ValidationError):
        Assumptions(start_year=2026, end_year=2030, fx_rates={"CAD": 0})


def test_run_converts_value_to_usd(sample_portfolio):
    p = _with_account(sample_portfolio)
    working, warnings = apply_scenario(p, _base(p))
    cad = next(a for a in working.assets if a.asset_id == "cad")
    assert cad.initial_value == pytest.approx(72_000)            # C$100,000 x 0.72
    assert next(a for a in p.assets if a.asset_id == "cad").initial_value == 100_000   # stored portfolio unchanged
    assert not [w for w in warnings if "exchange rate" in w]


def test_its_own_cashflows_are_converted_too(sample_portfolio):
    from models import Cashflow, CashflowDirection, FlowType
    p = _with_account(sample_portfolio)
    p.add_cashflow(Cashflow(flow_id="cad_fee", name="Account fee", flow_type=FlowType.OPERATING_EXPENSE,
                            direction=CashflowDirection.OUTFLOW, amount=100, start_year=2026, asset_id="cad"))
    working, _ = apply_scenario(p, _base(p))
    assert next(cf for cf in working.cashflows if cf.flow_id == "cad_fee").amount == pytest.approx(72)


def test_missing_rate_warns_and_leaves_the_amount(sample_portfolio):
    p = _with_account(sample_portfolio, rate=None)
    working, warnings = apply_scenario(p, _base(p))
    assert any("no CAD exchange rate" in w for w in warnings)
    assert next(a for a in working.assets if a.asset_id == "cad").initial_value == 100_000


def test_net_worth_counts_it_in_usd_and_it_compounds(sample_portfolio):
    p = _with_account(sample_portfolio)
    res = simulation.run_simulation(p, _base(p)).annual_results
    cad = lambda r: next(a for a in r.asset_results if a.asset_id == "cad")
    assert cad(res[0]).beginning_value == pytest.approx(72_000)
    assert cad(res[0]).growth_amount == pytest.approx(72_000 * 0.04)


def test_interest_taxed_yearly_raises_the_tax(sample_portfolio):
    import copy
    taxed = _with_account(copy.deepcopy(sample_portfolio), interest_taxed_yearly=True)
    untaxed = _with_account(copy.deepcopy(sample_portfolio), interest_taxed_yearly=False)
    taxed.assumptions.tax_method = untaxed.assumptions.tax_method = "detailed"
    t = simulation.run_simulation(taxed, _base(taxed)).annual_results[0]
    u = simulation.run_simulation(untaxed, _base(untaxed)).annual_results[0]
    assert t.total_estimated_tax > u.total_estimated_tax
    # ...but the interest isn't spendable cash: gross income is the same
    assert t.total_gross_income == pytest.approx(u.total_gross_income)


def test_currency_survives_save_and_load(sample_portfolio):
    p = _with_account(sample_portfolio, interest_taxed_yearly=True)
    again = Portfolio.from_dict(p.to_dict())
    a = next(x for x in again.assets if x.asset_id == "cad")
    assert (a.currency, a.interest_taxed_yearly, again.assumptions.fx_rates) == ("CAD", True, {"CAD": 0.72})
