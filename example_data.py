"""
example_data.py

Builds a FICTIONAL household that exercises every v2 feature, so you can
open it in the app and watch each one work -- and so tests/test_example.py
can check its numbers against hand arithmetic.

    python example_data.py      ->  writes data/example_transition.json

Alex & Sam Example (fictional):
  People      Alex, born Jul 1965 | Sam, born Jul 1967 (both planned to age 92)
  Events      Alex retires at 61 (Jul 2026) | Sam retires at 63 (Jul 2030) | Sell rental Jan 2034
  Assets      Brokerage $400k (equity) | Savings $60k (cash) | Elm St rental $350k
              (depreciation: $250k basis from 2015 over 27.5 yrs = $9,091/yr; the rental
              is sold in 2034, before it runs out in 2042)
  Liabilities Elm St mortgage $180k at 4.5%, 240 months left (secured by the rental)
              HELOC $20k at 8%, interest-only
  Cashflows   Alex salary $120k  -> ends at Alex retires
              Alex pension $48k  -> starts at Alex retires, 2% COLA
              Sam salary $70k    -> ends at Sam retires
              Sam pension $20k   -> starts at Sam retires, 2% COLA
              Elm St rent $2,200/month, expenses $6,000/yr -> end when the rental sells
              Elm St sale proceeds $420k one-time at the sale
              Living expenses $90k/yr, inflation adjusted
  Scenarios   Base | Alex retires 2028 instead | Higher inflation (4.5%) | Sam retires at 65 | Sam claims at 67

Also (for the manual and the Explore tab; all start 2027 or later):
  Entities    Alex & Sam (joint) owns 100% of the Alex & Sam Family Trust and of Elm Street LLC.
              Harbor Partners LP is owned 15% by the trust and 5% by the LLC -> 20% on two paths.
  Collection  "Rentals" (Elm Street LLC): the Elm St rental and the Oak Ave duplex.
  Oak Ave     bought Jan 2027 for $520k: gross rent $43,200, 5% vacancy, expenses by type;
              mortgage $400k at 6.25% for 30 years, first payment Feb 2027.
  Harbor      Harbor Plaza (commercial, $4M at 100%), distributions $200k/yr at 100% from 2028.
  Annuity     Sam's annuity $150k; pays $9,000/yr from Jul 2031: half taxable, half return of principal.
  Social Sec. Alex claims at 67 (Jul 2032) $36,000/yr; Sam claims at 62 (Jul 2029) $18,000/yr while
              still working -> the earnings test withholds benefits until Sam retires.
  Actuals     2026 actual: Elm St expenses $7,250 (confirmed; roof repair).
              2027 forecast: Elm St rent $27,500 (estimated).

Hand-checkable numbers (see tests/test_example.py):
  2026 Alex salary   = 120,000 x 6/12 = 60,000
  2026 Alex pension  =  48,000 x 6/12 = 24,000
  Mortgage payment   = $1,138.77/month (any mortgage calculator: 180k, 4.5%, 20 yrs)
  HELOC interest     = 20,000 x 8% = 1,600/yr
  2034: rental sold -> mortgage paid off; only the HELOC is owed after
"""

from pathlib import Path

import storage
from models import (
    Actual, Asset, AssetType, Assumptions, Bundle, Cashflow, CashflowDirection, CreditLine, Entity, EntityType,
    Event, FlowType, Frequency, Household, LiabilityType, LiquidityCategory, Loan, Membership, Ownership, Person,
    Portfolio, Scenario, TaxCategory,
)

OUT = Path(__file__).parent / "data" / "example_transition.json"


def build_example_portfolio() -> Portfolio:
    def flow(flow_id, name, flow_type, direction, amount, **kw):
        return Cashflow(flow_id=flow_id, name=name, flow_type=flow_type, direction=direction,
                        amount=amount, start_year=kw.pop("start_year", 2026), **kw)

    return Portfolio(
        household=Household(name="Alex & Sam Example (fictional)", members=["Alex", "Sam"]),
        assumptions=Assumptions(
            start_year=2026, end_year=2050, inflation_rate=0.03,
            default_tax_rates={"ordinary_income": 0.22, "pension_income": 0.20, "rental_income": 0.22},
        ),
        entities=[Entity(entity_id="entity_joint", name="Alex & Sam (joint)", entity_type=EntityType.JOINT),
                  Entity(entity_id="entity_trust", name="Alex & Sam Family Trust", entity_type=EntityType.TRUST),
                  Entity(entity_id="entity_llc", name="Elm Street LLC", entity_type=EntityType.LLC),
                  Entity(entity_id="entity_harbor", name="Harbor Partners LP", entity_type=EntityType.PARTNERSHIP)],
        memberships=[Membership(entity_id="entity_joint", person_id="person_alex", role="owner"),
                     Membership(entity_id="entity_joint", person_id="person_sam", role="owner"),
                     Membership(entity_id="entity_trust", person_id="person_alex", role="trustee"),
                     Membership(entity_id="entity_trust", person_id="person_sam", role="trustee"),
                     Membership(entity_id="entity_llc", person_id="person_alex", role="manager")],
        ownerships=[Ownership(owner_entity_id="entity_joint", owned_type="entity", owned_id="entity_trust", percent=1.0),
                    Ownership(owner_entity_id="entity_joint", owned_type="entity", owned_id="entity_llc", percent=1.0),
                    Ownership(owner_entity_id="entity_trust", owned_type="entity", owned_id="entity_harbor",
                              percent=0.15),
                    Ownership(owner_entity_id="entity_llc", owned_type="entity", owned_id="entity_harbor",
                              percent=0.05)],
        bundles=[Bundle(bundle_id="bundle_rentals", name="Rentals", entity_id="entity_llc",
                        asset_ids=["asset_elm_rental", "asset_oak"])],
        people=[
            Person(person_id="person_alex", name="Alex", birth_year=1965, birth_month=7, life_expectancy=92),
            Person(person_id="person_sam", name="Sam", birth_year=1967, birth_month=7, life_expectancy=92),
        ],
        events=[
            # Age-based: the date comes from the birthday (year/month here are filled in automatically).
            Event(event_id="event_alex_retires", name="Alex retires", year=0, person_id="person_alex", age=61),
            Event(event_id="event_sam_retires", name="Sam retires", year=0, person_id="person_sam", age=63),
            Event(event_id="event_sell_elm", name="Sell Elm St rental", year=2034, month=1),
            Event(event_id="event_buy_oak", name="Buy Oak Ave duplex", year=2027, month=1),
            Event(event_id="event_harbor_dist", name="Harbor Plaza distributions start", year=2028, month=1),
            Event(event_id="event_sam_ss", name="Sam claims Social Security", year=0, person_id="person_sam", age=62),
            Event(event_id="event_alex_ss", name="Alex claims Social Security", year=0, person_id="person_alex", age=67),
            Event(event_id="event_annuity", name="Annuity payments start", year=2031, month=7),
        ],
        assets=[
            Asset(asset_id="asset_brokerage", name="Brokerage", asset_type=AssetType.EQUITY_FUND, owner="Joint",
                  initial_value=400_000, start_year=2026, annual_growth_rate=0.06,
                  liquidity_category=LiquidityCategory.LIQUID, entity_id="entity_joint"),
            Asset(asset_id="asset_savings", name="Savings", asset_type=AssetType.CASH, owner="Joint",
                  initial_value=60_000, start_year=2026, annual_growth_rate=0.02,
                  liquidity_category=LiquidityCategory.LIQUID, entity_id="entity_joint"),
            Asset(asset_id="asset_elm_rental", name="Elm St rental", asset_type=AssetType.REAL_ESTATE,
                  owner="Joint", initial_value=350_000, start_year=2026, annual_growth_rate=0.03,
                  liquidity_category=LiquidityCategory.ILLIQUID, tax_category=TaxCategory.RENTAL_INCOME,
                  entity_id="entity_llc", end_event_id="event_sell_elm",
                  depreciable_basis=250_000, placed_in_service_year=2015, recovery_years=27.5),
            Asset(asset_id="asset_oak", name="Oak Ave duplex", asset_type=AssetType.REAL_ESTATE, owner="Elm Street LLC",
                  initial_value=520_000, start_year=2027, annual_growth_rate=0.03,
                  tax_category=TaxCategory.RENTAL_INCOME, entity_id="entity_llc", start_event_id="event_buy_oak",
                  depreciable_basis=400_000, placed_in_service_year=2027, recovery_years=27.5),
            Asset(asset_id="asset_harbor", name="Harbor Plaza", asset_type=AssetType.REAL_ESTATE, owner="Harbor Partners LP",
                  initial_value=4_000_000, start_year=2026, annual_growth_rate=0.02, entity_id="entity_harbor",
                  tax_category=TaxCategory.BUSINESS_INCOME, notes="Commercial; value at 100%"),
            Asset(asset_id="asset_annuity", name="Sam annuity", asset_type=AssetType.ANNUITY, owner="Sam",
                  initial_value=150_000, start_year=2026, liquidity_category=LiquidityCategory.INCOME_ONLY,
                  tax_category=TaxCategory.ANNUITY_INCOME, entity_id="entity_joint"),
        ],
        liabilities=[
            Loan(liability_id="liability_elm_mortgage", name="Elm St mortgage", balance=180_000,
                 annual_interest_rate=0.045, start_year=2026, term_months=240, lender="Example Bank",
                 liability_type=LiabilityType.MORTGAGE, secured_by_asset_id="asset_elm_rental",
                 entity_id="entity_joint"),
            Loan.from_original_terms(
                liability_id="liability_oak_mortgage", name="Oak Ave mortgage", original_amount=400_000,
                annual_interest_rate=0.0625, original_term_months=360, origination_year=2027, origination_month=2,
                as_of_year=2027, lender="Example Credit Union", liability_type=LiabilityType.MORTGAGE,
                secured_by_asset_id="asset_oak", entity_id="entity_llc"),
            CreditLine(liability_id="liability_heloc", name="HELOC", balance=20_000, annual_interest_rate=0.08,
                       start_year=2026, credit_limit=100_000, liability_type=LiabilityType.HELOC,
                       entity_id="entity_joint"),
        ],
        cashflows=[
            flow("flow_alex_salary", "Alex salary", FlowType.SALARY_INCOME, CashflowDirection.INFLOW, 120_000,
                 start_year=2020, taxable=True, tax_category=TaxCategory.ORDINARY_INCOME,
                 end_event_id="event_alex_retires", person_id="person_alex"),
            flow("flow_alex_pension", "Alex pension", FlowType.PENSION_INCOME, CashflowDirection.INFLOW, 48_000,
                 annual_growth_rate=0.02, taxable=True, tax_category=TaxCategory.PENSION_INCOME,
                 start_event_id="event_alex_retires", person_id="person_alex"),
            flow("flow_sam_salary", "Sam salary", FlowType.SALARY_INCOME, CashflowDirection.INFLOW, 70_000,
                 start_year=2020, taxable=True, tax_category=TaxCategory.ORDINARY_INCOME,
                 end_event_id="event_sam_retires", person_id="person_sam"),
            flow("flow_sam_pension", "Sam pension", FlowType.PENSION_INCOME, CashflowDirection.INFLOW, 20_000,
                 annual_growth_rate=0.02, taxable=True, tax_category=TaxCategory.PENSION_INCOME,
                 start_event_id="event_sam_retires", person_id="person_sam"),
            flow("flow_elm_rent", "Elm St rent", FlowType.RENT_INCOME, CashflowDirection.INFLOW, 2_200,
                 frequency=Frequency.MONTHLY, annual_growth_rate=0.03, taxable=True,
                 tax_category=TaxCategory.RENTAL_INCOME, asset_id="asset_elm_rental",
                 end_event_id="event_sell_elm"),
            flow("flow_elm_expenses", "Elm St expenses", FlowType.OPERATING_EXPENSE, CashflowDirection.OUTFLOW,
                 6_000, annual_growth_rate=0.03, asset_id="asset_elm_rental", end_event_id="event_sell_elm"),
            flow("flow_elm_sale", "Elm St sale proceeds", FlowType.SALE_PROCEEDS, CashflowDirection.INFLOW,
                 420_000, frequency=Frequency.ONE_TIME, asset_id="asset_elm_rental",
                 start_event_id="event_sell_elm"),
            flow("flow_living", "Living expenses", FlowType.LIVING_EXPENSE, CashflowDirection.OUTFLOW, 90_000,
                 inflation_adjusted=True),
            # Oak Ave duplex, entered the way "Add a rental property" does it
            flow("flow_oak_rent", "Oak Ave - Gross scheduled rent", FlowType.RENT_INCOME, CashflowDirection.INFLOW,
                 43_200, start_year=2027, annual_growth_rate=0.03, taxable=True,
                 tax_category=TaxCategory.RENTAL_INCOME, asset_id="asset_oak"),
            flow("flow_oak_vacancy", "Oak Ave - Vacancy & credit loss", FlowType.OPERATING_EXPENSE,
                 CashflowDirection.OUTFLOW, 2_160, start_year=2027, annual_growth_rate=0.03, asset_id="asset_oak"),
            flow("flow_oak_tax", "Oak Ave - Property tax", FlowType.PROPERTY_TAX, CashflowDirection.OUTFLOW, 6_500,
                 start_year=2027, annual_growth_rate=0.02, asset_id="asset_oak"),
            flow("flow_oak_insurance", "Oak Ave - Insurance", FlowType.INSURANCE, CashflowDirection.OUTFLOW, 2_400,
                 start_year=2027, annual_growth_rate=0.04, asset_id="asset_oak"),
            flow("flow_oak_repairs", "Oak Ave - Repairs & maintenance", FlowType.REPAIRS, CashflowDirection.OUTFLOW,
                 3_000, start_year=2027, annual_growth_rate=0.03, asset_id="asset_oak"),
            flow("flow_oak_mgmt", "Oak Ave - Property management", FlowType.MANAGEMENT_FEE, CashflowDirection.OUTFLOW,
                 3_456, start_year=2027, annual_growth_rate=0.03, asset_id="asset_oak"),
            # Harbor Plaza at 100%; your 20% share is applied automatically
            flow("flow_harbor", "Harbor Plaza distributions", FlowType.BUSINESS_DISTRIBUTION, CashflowDirection.INFLOW,
                 200_000, annual_growth_rate=0.02, taxable=True, tax_category=TaxCategory.BUSINESS_INCOME,
                 asset_id="asset_harbor", start_event_id="event_harbor_dist"),
            # Annuity: half of each payment taxable, half a return of principal
            flow("flow_annuity_taxable", "Sam annuity - taxable part", FlowType.ANNUITY_INCOME,
                 CashflowDirection.INFLOW, 375, frequency=Frequency.MONTHLY, taxable=True,
                 tax_category=TaxCategory.ANNUITY_INCOME, asset_id="asset_annuity",
                 start_event_id="event_annuity", person_id="person_sam"),
            flow("flow_annuity_principal", "Sam annuity - return of principal", FlowType.ANNUITY_INCOME,
                 CashflowDirection.INFLOW, 375, frequency=Frequency.MONTHLY, taxable=False,
                 asset_id="asset_annuity", start_event_id="event_annuity", person_id="person_sam"),
            # Social Security
            flow("flow_alex_ss", "Alex Social Security", FlowType.SOCIAL_SECURITY_INCOME, CashflowDirection.INFLOW,
                 3_000, frequency=Frequency.MONTHLY, annual_growth_rate=0.025, start_event_id="event_alex_ss",
                 person_id="person_alex"),
            flow("flow_sam_ss", "Sam Social Security", FlowType.SOCIAL_SECURITY_INCOME, CashflowDirection.INFLOW,
                 1_500, frequency=Frequency.MONTHLY, annual_growth_rate=0.025, start_event_id="event_sam_ss",
                 person_id="person_sam"),
        ],
        actuals=[
            Actual(actual_id="actual_elm_exp_2026", year=2026, amount=7_250, name="Elm St expenses", kind="actual",
                   direction="outflow", category="operating_expense", flow_id="flow_elm_expenses",
                   asset_id="asset_elm_rental", status="confirmed", record_class="Elm 2026",
                   notes="Includes a $1,250 roof repair"),
            Actual(actual_id="actual_elm_rent_2027", year=2027, amount=27_500, name="Elm St rent", kind="forecast",
                   category="rent_income", flow_id="flow_elm_rent", asset_id="asset_elm_rental",
                   status="estimated", notes="New lease from March"),
        ],
        scenarios=[
            Scenario(scenario_id="base", name="Base", is_base_case=True),
            Scenario(scenario_id="alex_retires_2028", name="Alex retires Jan 2028",
                     event_overrides={"event_alex_retires": {"year": 2028, "month": 1}}),
            Scenario(scenario_id="higher_inflation", name="Higher inflation (4.5%)",
                     inflation_rate_override=0.045),
            Scenario(scenario_id="sam_retires_65", name="Sam retires at 65",
                     event_overrides={"event_sam_retires": {"age": 65}}),
            Scenario(scenario_id="sam_claims_67", name="Sam claims Social Security at 67",
                     event_overrides={"event_sam_ss": {"age": 67}}),
        ],
        metadata_notes="Fictional example household for learning and testing. Not real data.",
    )


if __name__ == "__main__":
    storage.save_portfolio(build_example_portfolio(), OUT, make_backup=False)
    print(f"Example portfolio written to {OUT}")
