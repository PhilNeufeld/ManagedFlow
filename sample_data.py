"""
sample_data.py

Builds a representative (entirely fictional) sample Portfolio covering
every asset type and a useful spread of cashflow types, so the rest of the
application can be developed and demoed without any real personal data.

Run this module directly to write data/portfolio_sample.json:
    python sample_data.py
"""

from __future__ import annotations

from pathlib import Path

from models import (
    Asset,
    AssetType,
    Assumptions,
    Bundle,
    Cashflow,
    CashflowDirection,
    Entity,
    EntityType,
    FlowType,
    Frequency,
    Household,
    LiquidityCategory,
    Portfolio,
    Scenario,
    TaxCategory,
)
import storage


def build_sample_portfolio() -> Portfolio:
    household = Household(name="Sample Household", members=["Spouse A", "Spouse B"])
    assumptions = Assumptions(start_year=2026, end_year=2055, inflation_rate=0.03)

    entities = [
        Entity(
            entity_id="entity_personal",
            name="Spouse A & Spouse B (Personal/Joint)",
            entity_type=EntityType.JOINT,
            notes="Personally/jointly held assets and income.",
        ),
        Entity(
            entity_id="entity_rental_llc",
            name="Sample Rental Property LLC",
            entity_type=EntityType.LLC,
            notes="Single-purpose LLC holding the sample rental property.",
        ),
        Entity(
            entity_id="entity_business_llc",
            name="Sample Business Interests LLC",
            entity_type=EntityType.LLC,
            notes="Holds the sample illiquid business interest.",
        ),
        Entity(
            entity_id="entity_family_trust",
            name="Sample Family Trust",
            entity_type=EntityType.TRUST,
            notes="Revocable family trust holding the sample trust asset.",
        ),
    ]

    # Each _build_* helper returns (asset, its cashflows). Assets go in
    # portfolio.assets; their cashflows go in the one top-level
    # portfolio.cashflows list, linked back by asset_id.
    built = [
        _build_rental_property(),
        _build_equity_fund(),
        _build_cash_account(),
        _build_annuity(),
        _build_pension(),
        _build_business_interest(),
        _build_trust(),
    ]
    assets = [asset for asset, _ in built]
    asset_cashflows = []
    for asset, flows in built:
        for cf in flows:
            cf.asset_id = asset.asset_id
            asset_cashflows.append(cf)
    # Attribute each asset to its holding entity. Done here (rather than
    # inside each _build_* function) so the entity assignment is visible
    # in one place alongside the Bundle layout below.
    _asset_by_id = {a.asset_id: a for a in assets}
    _asset_by_id["real_estate_rental_1"].entity_id = "entity_rental_llc"
    _asset_by_id["equity_fund_1"].entity_id = "entity_personal"
    _asset_by_id["cash_1"].entity_id = "entity_personal"
    _asset_by_id["annuity_1"].entity_id = "entity_personal"
    _asset_by_id["pension_1"].entity_id = "entity_personal"
    _asset_by_id["business_1"].entity_id = "entity_business_llc"
    _asset_by_id["trust_1"].entity_id = "entity_family_trust"

    household_cashflows = [
        Cashflow(
            flow_id="hh_salary_a",
            name="Salary - Spouse A (employer)",
            flow_type=FlowType.SALARY_INCOME,
            direction=CashflowDirection.INFLOW,
            amount=95000,
            start_year=2026,
            end_year=2028,
            frequency=Frequency.ANNUAL,
            annual_growth_rate=0.02,
            taxable=True,
            tax_category=TaxCategory.ORDINARY_INCOME,
            notes="Ends at assumed retirement transition year.",
        ),
        Cashflow(
            flow_id="hh_pars_a",
            name="PARS supplemental retirement income - Spouse A",
            flow_type=FlowType.PARS_INCOME,
            direction=CashflowDirection.INFLOW,
            amount=12000,
            start_year=2029,
            frequency=Frequency.ANNUAL,
            taxable=True,
            tax_category=TaxCategory.ORDINARY_INCOME,
        ),
        Cashflow(
            flow_id="hh_social_security_a",
            name="Social Security - Spouse A",
            flow_type=FlowType.SOCIAL_SECURITY_INCOME,
            direction=CashflowDirection.INFLOW,
            amount=28000,
            start_year=2033,
            frequency=Frequency.ANNUAL,
            inflation_adjusted=True,
            taxable=True,
            tax_category=TaxCategory.ORDINARY_INCOME,
        ),
        Cashflow(
            flow_id="hh_living_expenses",
            name="Core household living expenses",
            flow_type=FlowType.LIVING_EXPENSE,
            direction=CashflowDirection.OUTFLOW,
            amount=72000,
            start_year=2026,
            frequency=Frequency.ANNUAL,
            annual_growth_rate=0.03,
            inflation_adjusted=True,
            notes="Groceries, utilities, insurance, travel, general spending.",
        ),
        Cashflow(
            flow_id="hh_estimated_tax_payment",
            name="Estimated household tax payment placeholder",
            flow_type=FlowType.TAX_PAYMENT,
            direction=CashflowDirection.OUTFLOW,
            amount=0,
            start_year=2026,
            frequency=Frequency.ANNUAL,
            notes="Left at 0; total_estimated_tax is computed separately by taxes.py.",
        ),
    ]

    bundles = [
        Bundle(
            bundle_id="bundle_rental_1",
            name="Rental Address 1",
            entity_id="entity_rental_llc",
            asset_ids=["real_estate_rental_1"],
            notes="The rental property together with its loan, appreciation, "
                  "depreciation, rent income, and operating expenses (all "
                  "already attached as cashflows on the asset itself).",
        ),
        Bundle(
            bundle_id="bundle_investment_portfolio",
            name="Investment Portfolio",
            entity_id="entity_personal",
            asset_ids=["equity_fund_1", "cash_1"],
        ),
        Bundle(
            bundle_id="bundle_retirement_income",
            name="Retirement Income Sources",
            entity_id="entity_personal",
            asset_ids=["annuity_1", "pension_1"],
        ),
        Bundle(
            bundle_id="bundle_spouse_a_earnings",
            name="Spouse A Earnings",
            entity_id="entity_personal",
            cashflow_ids=["hh_salary_a", "hh_pars_a", "hh_social_security_a"],
        ),
        Bundle(
            bundle_id="bundle_household_expenses",
            name="Household Living Expenses",
            entity_id="entity_personal",
            cashflow_ids=["hh_living_expenses", "hh_estimated_tax_payment"],
        ),
        Bundle(
            bundle_id="bundle_business_interest",
            name="Business Interest",
            entity_id="entity_business_llc",
            asset_ids=["business_1"],
        ),
        Bundle(
            bundle_id="bundle_family_trust",
            name="Family Trust",
            entity_id="entity_family_trust",
            asset_ids=["trust_1"],
        ),
    ]

    scenarios = [
        Scenario(
            scenario_id="base_case",
            name="Base Case",
            description="Assumptions and assets as entered, no overrides.",
            is_base_case=True,
        ),
        Scenario(
            scenario_id="higher_inflation",
            name="Higher Inflation",
            description="Stress test with elevated inflation.",
            inflation_rate_override=0.05,
        ),
        Scenario(
            scenario_id="sell_rental_2032",
            name="Sell One Rental",
            description="Sell the sample rental property in 2032 instead of holding indefinitely.",
            asset_overrides={
                "real_estate_rental_1": {"sale_year": 2032},
            },
        ),
        Scenario(
            scenario_id="lower_market_returns",
            name="Lower Market Returns",
            description="Reduced equity fund growth assumption.",
            asset_overrides={
                "equity_fund_1": {"annual_growth_rate": 0.04},
            },
        ),
        Scenario(
            scenario_id="rental_llc_only",
            name="Rental LLC Only",
            description="Demonstrates Entity-scoped simulation: runs only the "
                         "Bundle(s) under the Rental Property LLC, ignoring "
                         "every other asset and cashflow in the portfolio.",
            entity_ids=["entity_rental_llc"],
        ),
        Scenario(
            scenario_id="spouse_a_earnings_only",
            name="Spouse A Earnings Only",
            description="Demonstrates Bundle-scoped simulation: runs only the "
                         "Spouse A Earnings bundle (a cashflow-only bundle with "
                         "no asset attached).",
            bundle_ids=["bundle_spouse_a_earnings"],
        ),
    ]

    portfolio = Portfolio(
        household=household,
        assumptions=assumptions,
        entities=entities,
        assets=assets,
        cashflows=household_cashflows + asset_cashflows,
        bundles=bundles,
        scenarios=scenarios,
        metadata_notes="Sample/demo data. Contains no real personal or financial information.",
    )
    return portfolio


def _build_rental_property() -> tuple:
    asset = Asset(
        asset_id="real_estate_rental_1",
        name="Sample Rental Property",
        asset_type=AssetType.REAL_ESTATE,
        owner="Joint",
        initial_value=450000,
        start_year=2026,
        annual_growth_rate=0.03,
        liquidity_category=LiquidityCategory.ILLIQUID,
        tax_category=TaxCategory.RENTAL_INCOME,
        notes="Single-family rental; sample numbers only.",
    )
    flows = [
        Cashflow(
            flow_id="rental1_rent_income",
            asset_id=asset.asset_id,
            name="Rent income",
            flow_type=FlowType.RENT_INCOME,
            direction=CashflowDirection.INFLOW,
            amount=2800,
            start_year=2026,
            frequency=Frequency.MONTHLY,
            annual_growth_rate=0.025,
            taxable=True,
            tax_category=TaxCategory.RENTAL_INCOME,
        ),
        Cashflow(
            flow_id="rental1_mortgage_interest",
            asset_id=asset.asset_id,
            name="Mortgage interest",
            flow_type=FlowType.LOAN_INTEREST,
            direction=CashflowDirection.OUTFLOW,
            amount=900,
            start_year=2026,
            end_year=2041,
            frequency=Frequency.MONTHLY,
            annual_growth_rate=-0.02,  # interest portion declines as loan amortizes
        ),
        Cashflow(
            flow_id="rental1_mortgage_principal",
            asset_id=asset.asset_id,
            name="Mortgage principal repayment",
            flow_type=FlowType.LOAN_PRINCIPAL_REPAYMENT,
            direction=CashflowDirection.OUTFLOW,
            amount=550,
            start_year=2026,
            end_year=2041,
            frequency=Frequency.MONTHLY,
            annual_growth_rate=0.02,
            affects_debt_balance=True,
        ),
        Cashflow(
            flow_id="rental1_property_tax",
            asset_id=asset.asset_id,
            name="Property tax",
            flow_type=FlowType.PROPERTY_TAX,
            direction=CashflowDirection.OUTFLOW,
            amount=5400,
            start_year=2026,
            frequency=Frequency.ANNUAL,
            annual_growth_rate=0.02,
        ),
        Cashflow(
            flow_id="rental1_insurance",
            asset_id=asset.asset_id,
            name="Insurance",
            flow_type=FlowType.INSURANCE,
            direction=CashflowDirection.OUTFLOW,
            amount=2200,
            start_year=2026,
            frequency=Frequency.ANNUAL,
            annual_growth_rate=0.04,
        ),
        Cashflow(
            flow_id="rental1_repairs",
            asset_id=asset.asset_id,
            name="Repairs and maintenance",
            flow_type=FlowType.REPAIRS,
            direction=CashflowDirection.OUTFLOW,
            amount=3500,
            start_year=2026,
            frequency=Frequency.ANNUAL,
            annual_growth_rate=0.03,
        ),
        Cashflow(
            flow_id="rental1_management_fee",
            asset_id=asset.asset_id,
            name="Property management fee",
            flow_type=FlowType.MANAGEMENT_FEE,
            direction=CashflowDirection.OUTFLOW,
            amount=2000,
            start_year=2026,
            frequency=Frequency.ANNUAL,
            annual_growth_rate=0.025,
        ),
        Cashflow(
            flow_id="rental1_depreciation",
            asset_id=asset.asset_id,
            name="Depreciation (noncash placeholder)",
            flow_type=FlowType.NONCASH_DEPRECIATION,
            direction=CashflowDirection.NONCASH,
            amount=14500,
            start_year=2026,
            end_year=2052,
            frequency=Frequency.ANNUAL,
            notes="Placeholder only; not wired into the tax estimate in the MVP.",
        ),
        Cashflow(
            flow_id="rental1_sale_proceeds",
            asset_id=asset.asset_id,
            name="Sale proceeds (only realized if scenario sets sale_year)",
            flow_type=FlowType.SALE_PROCEEDS,
            direction=CashflowDirection.INFLOW,
            amount=0,
            start_year=2026,
            frequency=Frequency.ONE_TIME,
            taxable=True,
            tax_category=TaxCategory.CAPITAL_GAINS,
            notes="Amount is 0 unless a scenario overrides it alongside sale_year.",
        ),
    ]
    return asset, flows


def _build_equity_fund() -> tuple:
    asset = Asset(
        asset_id="equity_fund_1",
        name="Sample Taxable Brokerage Account",
        asset_type=AssetType.EQUITY_FUND,
        owner="Joint",
        initial_value=620000,
        start_year=2026,
        annual_growth_rate=0.06,
        liquidity_category=LiquidityCategory.LIQUID,
        tax_category=TaxCategory.CAPITAL_GAINS,
        notes="Diversified index fund portfolio; sample numbers only.",
    )
    flows = [
        Cashflow(
            flow_id="equity1_dividends",
            asset_id=asset.asset_id,
            name="Qualified dividends",
            flow_type=FlowType.DIVIDEND_INCOME,
            direction=CashflowDirection.INFLOW,
            amount=9500,
            start_year=2026,
            frequency=Frequency.ANNUAL,
            annual_growth_rate=0.03,
            taxable=True,
            tax_category=TaxCategory.QUALIFIED_DIVIDENDS,
        ),
        Cashflow(
            flow_id="equity1_reinvested_dividends",
            asset_id=asset.asset_id,
            name="Reinvested dividends",
            flow_type=FlowType.DIVIDEND_INCOME,
            direction=CashflowDirection.TRANSFER,
            amount=9500,
            start_year=2026,
            frequency=Frequency.ANNUAL,
            annual_growth_rate=0.03,
            affects_asset_value=True,
            notes="Mirrors equity1_dividends; modeled as a transfer back into the fund.",
        ),
        Cashflow(
            flow_id="equity1_capital_gains_distributions",
            asset_id=asset.asset_id,
            name="Capital gains distributions",
            flow_type=FlowType.DIVIDEND_INCOME,
            direction=CashflowDirection.INFLOW,
            amount=4000,
            start_year=2026,
            frequency=Frequency.ANNUAL,
            taxable=True,
            tax_category=TaxCategory.CAPITAL_GAINS,
        ),
        Cashflow(
            flow_id="equity1_management_fee",
            asset_id=asset.asset_id,
            name="Management / advisory fee",
            flow_type=FlowType.MANAGEMENT_FEE,
            direction=CashflowDirection.OUTFLOW,
            amount=4500,
            start_year=2026,
            frequency=Frequency.ANNUAL,
            annual_growth_rate=0.03,
        ),
    ]
    return asset, flows


def _build_cash_account() -> tuple:
    asset = Asset(
        asset_id="cash_1",
        name="Sample Cash / Money Market Reserve",
        asset_type=AssetType.CASH,
        owner="Joint",
        initial_value=85000,
        start_year=2026,
        annual_growth_rate=0.02,
        liquidity_category=LiquidityCategory.LIQUID,
        tax_category=TaxCategory.ORDINARY_INCOME,
        notes="Emergency reserve / first-line liquidity buffer.",
    )
    flows = [
        Cashflow(
            flow_id="cash1_interest",
            asset_id=asset.asset_id,
            name="Interest income",
            flow_type=FlowType.INTEREST_INCOME,
            direction=CashflowDirection.INFLOW,
            amount=1700,
            start_year=2026,
            frequency=Frequency.ANNUAL,
            taxable=True,
            tax_category=TaxCategory.ORDINARY_INCOME,
        ),
    ]
    return asset, flows


def _build_annuity() -> tuple:
    asset = Asset(
        asset_id="annuity_1",
        name="Sample Fixed Annuity",
        asset_type=AssetType.ANNUITY,
        owner="Spouse B",
        initial_value=150000,
        start_year=2026,
        annual_growth_rate=0.0,
        liquidity_category=LiquidityCategory.RESTRICTED,
        tax_category=TaxCategory.ANNUITY_INCOME,
        do_not_sell=False,
        notes="Guaranteed income rider begins 2030 in this sample.",
    )
    flows = [
        Cashflow(
            flow_id="annuity1_guaranteed_income",
            asset_id=asset.asset_id,
            name="Guaranteed income payments",
            flow_type=FlowType.ANNUITY_INCOME,
            direction=CashflowDirection.INFLOW,
            amount=9000,
            start_year=2030,
            frequency=Frequency.ANNUAL,
            taxable=True,
            tax_category=TaxCategory.ANNUITY_INCOME,
        ),
    ]
    return asset, flows


def _build_pension() -> tuple:
    asset = Asset(
        asset_id="pension_1",
        name="Sample CalPERS-style Pension",
        asset_type=AssetType.PENSION,
        owner="Spouse A",
        initial_value=0,
        start_year=2026,
        annual_growth_rate=0.0,
        liquidity_category=LiquidityCategory.INCOME_ONLY,
        tax_category=TaxCategory.PENSION_INCOME,
        notes="Pension has no market value to track; modeled purely as an income stream.",
    )
    flows = [
        Cashflow(
            flow_id="pension1_income",
            asset_id=asset.asset_id,
            name="Pension income",
            flow_type=FlowType.PENSION_INCOME,
            direction=CashflowDirection.INFLOW,
            amount=42000,
            start_year=2029,
            frequency=Frequency.ANNUAL,
            annual_growth_rate=0.02,
            taxable=True,
            tax_category=TaxCategory.PENSION_INCOME,
            notes="Scenario 'delay_calpers' / 'start_calpers_immediately' would override start_year.",
        ),
    ]
    return asset, flows


def _build_business_interest() -> tuple:
    asset = Asset(
        asset_id="business_1",
        name="Sample Minority Business Interest",
        asset_type=AssetType.BUSINESS,
        owner="Spouse A",
        initial_value=180000,
        start_year=2026,
        annual_growth_rate=0.04,
        liquidity_category=LiquidityCategory.ILLIQUID,
        tax_category=TaxCategory.BUSINESS_INCOME,
        do_not_sell=True,
        notes="Illustrates an illiquid, do-not-sell holding with periodic distributions.",
    )
    flows = [
        Cashflow(
            flow_id="business1_distribution",
            asset_id=asset.asset_id,
            name="Annual distribution",
            flow_type=FlowType.BUSINESS_DISTRIBUTION,
            direction=CashflowDirection.INFLOW,
            amount=8000,
            start_year=2026,
            frequency=Frequency.ANNUAL,
            taxable=True,
            tax_category=TaxCategory.BUSINESS_INCOME,
        ),
    ]
    return asset, flows


def _build_trust() -> tuple:
    asset = Asset(
        asset_id="trust_1",
        name="Sample Family Trust",
        asset_type=AssetType.TRUST,
        owner="Joint",
        initial_value=95000,
        start_year=2026,
        annual_growth_rate=0.045,
        liquidity_category=LiquidityCategory.RESTRICTED,
        tax_category=TaxCategory.TRUST_INCOME,
        notes="Placeholder structure for a charitable remainder trust or similar vehicle.",
    )
    flows = [
        Cashflow(
            flow_id="trust1_distribution",
            asset_id=asset.asset_id,
            name="Beneficiary distribution",
            flow_type=FlowType.TRUST_DISTRIBUTION,
            direction=CashflowDirection.INFLOW,
            amount=3000,
            start_year=2026,
            frequency=Frequency.ANNUAL,
            taxable=True,
            tax_category=TaxCategory.TRUST_INCOME,
        ),
        Cashflow(
            flow_id="trust1_fees",
            asset_id=asset.asset_id,
            name="Trustee / administration fees",
            flow_type=FlowType.MANAGEMENT_FEE,
            direction=CashflowDirection.OUTFLOW,
            amount=900,
            start_year=2026,
            frequency=Frequency.ANNUAL,
        ),
    ]
    return asset, flows


if __name__ == "__main__":
    portfolio = build_sample_portfolio()
    out_path = Path(__file__).parent / "data" / "portfolio_sample.json"
    storage.save_portfolio(portfolio, out_path, make_backup=False)
    print(f"Sample portfolio written to {out_path}")
