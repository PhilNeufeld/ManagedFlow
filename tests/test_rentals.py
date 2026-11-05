"""Rentals from gross operating income, with a mortgage. Numbers you can check by hand:

  4392 N Brix: gross rent $24,000/yr, 5% vacancy ($1,200) -> GOI $22,800
  Expenses: tax 4,000 + insurance 1,200 + repairs 1,800 + management 1,920 = $8,920 -> NOI $13,880
  Mortgage: $300,000 at 6% for 30 years, first payment Jan 2016
    payment = 300,000 x 0.005 / (1 - 1.005^-360) = $1,798.65/month ($21,583.82/yr)
    as of Jan 2026: 120 payments made, 240 left, balance $251,057.17
  Depreciation: $275,000 / 27.5 = $10,000/yr
"""
import pytest

import exports
import rentals
import simulation
from models import (
    Assumptions, Cashflow, CashflowDirection, FlowType, Loan, Portfolio, Scenario, ValidationError,
    liability_from_dict,
)

YEARLY_PAYMENT = 1_798.6516 * 12


def household(**assumptions):
    return Portfolio(
        assumptions=Assumptions(start_year=2026, end_year=2030, inflation_rate=0.0, tax_method="detailed",
                                **assumptions),
        scenarios=[Scenario(scenario_id="base", name="Base", is_base_case=True)],
    )


def brix(**mortgage):
    terms = dict(annual_interest_rate=0.06, original_amount=300_000, term_years=30, first_payment_year=2016)
    terms.update(mortgage)
    return rentals.RentalSpec(
        name="4392 N Brix", value=400_000, start_year=2026, gross_rent=24_000, vacancy_rate=0.05,
        rent_growth=0.0, expense_growth=0.0, value_growth=0.0,
        expenses={"property_tax": 4_000, "insurance": 1_200, "repairs": 1_800, "management": 1_920},
        depreciable_basis=275_000, placed_in_service_year=2016,
        mortgage=rentals.MortgageSpec(**terms) if terms.get("annual_interest_rate") is not None else None)


# --- loans from original terms ------------------------------------------------------------------

def test_balance_and_payments_left_from_original_terms():
    loan = Loan.from_original_terms(liability_id="m", name="m", original_amount=300_000, annual_interest_rate=0.06,
                                    original_term_months=360, origination_year=2016, as_of_year=2026)
    assert loan.balance == pytest.approx(251_057.17, abs=0.01)
    assert loan.term_months == 240 and loan.months_left() == 240
    assert loan.scheduled_monthly_payment() == pytest.approx(1_798.65, abs=0.01)
    assert loan.payoff_date() == (2045, 12)     # 240th payment from Jan 2026


def test_mid_year_first_payment_counts_months():
    loan = Loan.from_original_terms(liability_id="m", name="m", original_amount=300_000, annual_interest_rate=0.06,
                                    original_term_months=360, origination_year=2016, origination_month=7,
                                    as_of_year=2026)
    assert loan.term_months == 246             # Jul 2016 -> Jan 2026 = 114 payments made


def test_a_rounded_statement_payment_does_not_add_a_month():
    loan = Loan(liability_id="m", name="m", balance=251_057.17, annual_interest_rate=0.06, start_year=2026,
                monthly_payment=1_798.65)
    assert loan.months_left() == 240


def test_fully_paid_loan_is_refused():
    with pytest.raises(ValidationError, match="all 120 payments"):
        Loan.from_original_terms(liability_id="m", name="m", original_amount=100_000, annual_interest_rate=0.05,
                                 original_term_months=120, origination_year=2010, as_of_year=2026)


def test_original_terms_survive_save_and_load_and_old_loans_still_load():
    loan = Loan.from_original_terms(liability_id="m", name="m", original_amount=300_000, annual_interest_rate=0.06,
                                    original_term_months=360, origination_year=2016, as_of_year=2026)
    back = liability_from_dict(loan.to_dict())
    assert (back.original_amount, back.original_term_months, back.origination_year) == (300_000, 360, 2016)
    old = {"kind": "loan", "liability_id": "o", "name": "o", "balance": 1000, "annual_interest_rate": 0.05,
           "start_year": 2026, "term_months": 12}
    assert liability_from_dict(old).original_amount is None


# --- the rental template ------------------------------------------------------------------------

def test_add_rental_creates_property_flows_and_mortgage():
    p = household()
    asset, flows, loan = rentals.add_rental(p, brix())
    assert asset.tax_category.value == "rental_income" and asset.depreciation_in_year(2026) == 10_000
    kinds = {cf.line_item: cf for cf in flows}
    assert kinds["rent"].amount == 24_000 and kinds["vacancy"].amount == 1_200
    assert kinds["property_tax"].flow_type == FlowType.PROPERTY_TAX
    assert isinstance(asset.asset_id, int) and all(isinstance(cf.flow_id, int) for cf in flows)
    assert isinstance(loan.liability_id, int)
    assert loan.secured_by_asset_id == asset.asset_id and loan.balance == pytest.approx(251_057.17, abs=0.01)
    assert p.get_liability(loan.liability_id) is loan


def test_current_balance_and_years_left_also_work():
    p = household()
    _, _, loan = rentals.add_rental(p, brix(original_amount=None, balance=251_057.17, years_left=20))
    assert loan.term_months == 240
    assert loan.scheduled_monthly_payment() == pytest.approx(1_798.65, abs=0.01)


def test_mortgage_without_terms_is_refused_and_nothing_is_left_behind():
    p = household()
    with pytest.raises(ValidationError, match="original terms"):
        rentals.add_rental(p, brix(original_amount=None))
    assert not p.assets and not p.cashflows and not p.liabilities


# --- the pro forma ---------------------------------------------------------------------------------

def test_pro_forma_goi_noi_debt_service_and_cash_flow():
    p = household()
    asset, _, _ = rentals.add_rental(p, brix())
    y = rentals.rental_pro_forma(p, p.get_scenario("base"), asset.asset_id)[0]
    assert y["Year"] == 2026
    assert y["Gross operating income (GOI)"] == pytest.approx(22_800)
    assert y["Total operating expenses"] == pytest.approx(8_920)
    assert y["Net operating income (NOI)"] == pytest.approx(13_880)
    assert y["Debt service"] == pytest.approx(YEARLY_PAYMENT, abs=0.05)
    assert y["Interest"] + y["Principal"] == pytest.approx(y["Debt service"])
    assert y["Principal"] == pytest.approx(251_057.17 - y["Loan balance (end of year)"], abs=0.01)
    assert y["Cash flow before tax"] == pytest.approx(13_880 - YEARLY_PAYMENT, abs=0.05)
    assert y["Debt coverage (NOI / debt service)"] == pytest.approx(13_880 / YEARLY_PAYMENT, rel=1e-4)
    assert y["Taxable net (NOI - interest - depreciation)"] == pytest.approx(13_880 - y["Interest"] - 10_000)


def test_interest_share_falls_and_principal_rises_each_year():
    p = household()
    asset, _, _ = rentals.add_rental(p, brix())
    rows = rentals.rental_pro_forma(p, p.get_scenario("base"), asset.asset_id)
    assert rows[1]["Interest"] < rows[0]["Interest"] and rows[1]["Principal"] > rows[0]["Principal"]


def test_tax_engine_deducts_expenses_interest_and_depreciation():
    p = household()
    p.add_cashflow(Cashflow(flow_id="salary", name="Salary", flow_type=FlowType.SALARY_INCOME,
                            direction=CashflowDirection.INFLOW, amount=90_000, start_year=2026, taxable=True))
    asset, _, _ = rentals.add_rental(p, brix())
    pf = rentals.rental_pro_forma(p, p.get_scenario("base"), asset.asset_id)[0]
    tax = simulation.run_simulation(p, p.get_scenario("base")).annual_results[0].tax_estimate.details
    assert tax["Rental net (before passive limits)"] == pytest.approx(pf["Taxable net (NOI - interest - depreciation)"])


def test_after_payoff_cash_flow_equals_noi():
    p = household()
    asset, _, loan = rentals.add_rental(p, brix(original_amount=None, balance=10_000, years_left=1))
    rows = {r["Year"]: r for r in rentals.rental_pro_forma(p, p.get_scenario("base"), asset.asset_id)}
    assert rows[2026]["Loan balance (end of year)"] == pytest.approx(0, abs=0.01)
    assert rows[2027]["Debt service"] == 0 and rows[2027]["Cash flow before tax"] == pytest.approx(13_880)
    assert rows[2027]["Debt coverage (NOI / debt service)"] is None


def test_export_has_rental_pro_forma_sheet(tmp_path):
    from openpyxl import load_workbook
    p = household()
    rentals.add_rental(p, brix())
    path = exports.export_to_excel(p, simulation.run_all_scenarios(p), tmp_path / "out.xlsx")
    wb = load_workbook(path)
    assert "Rental Pro Forma" in wb.sheetnames
    ws = wb["Rental Pro Forma"]
    assert ws["A4"].value == "4392 N Brix" and ws["A5"].value == "Line"
