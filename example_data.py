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
  Liabilities Elm St mortgage $180k at 4.5%, 240 months left (secured by the rental)
              HELOC $20k at 8%, interest-only
  Cashflows   Alex salary $120k  -> ends at Alex retires
              Alex pension $48k  -> starts at Alex retires, 2% COLA
              Sam salary $70k    -> ends at Sam retires
              Sam pension $20k   -> starts at Sam retires, 2% COLA
              Elm St rent $2,200/month, expenses $6,000/yr -> end when the rental sells
              Elm St sale proceeds $420k one-time at the sale
              Living expenses $90k/yr, inflation adjusted
  Scenarios   Base | Alex retires 2028 instead | Higher inflation (4.5%) | Sam retires at 65

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
    Asset, AssetType, Assumptions, Cashflow, CashflowDirection, CreditLine, Entity, EntityType, Event,
    FlowType, Frequency, Household, LiabilityType, LiquidityCategory, Loan, Person, Portfolio, Scenario,
    TaxCategory,
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
        entities=[Entity(entity_id="entity_joint", name="Alex & Sam (joint)", entity_type=EntityType.JOINT)],
        people=[
            Person(person_id="person_alex", name="Alex", birth_year=1965, birth_month=7, life_expectancy=92),
            Person(person_id="person_sam", name="Sam", birth_year=1967, birth_month=7, life_expectancy=92),
        ],
        events=[
            # Age-based: the date comes from the birthday (year/month here are filled in automatically).
            Event(event_id="event_alex_retires", name="Alex retires", year=0, person_id="person_alex", age=61),
            Event(event_id="event_sam_retires", name="Sam retires", year=0, person_id="person_sam", age=63),
            Event(event_id="event_sell_elm", name="Sell Elm St rental", year=2034, month=1),
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
                  entity_id="entity_joint", end_event_id="event_sell_elm"),
        ],
        liabilities=[
            Loan(liability_id="liability_elm_mortgage", name="Elm St mortgage", balance=180_000,
                 annual_interest_rate=0.045, start_year=2026, term_months=240, lender="Example Bank",
                 liability_type=LiabilityType.MORTGAGE, secured_by_asset_id="asset_elm_rental",
                 entity_id="entity_joint"),
            CreditLine(liability_id="liability_heloc", name="HELOC", balance=20_000, annual_interest_rate=0.08,
                       start_year=2026, credit_limit=100_000, liability_type=LiabilityType.HELOC,
                       entity_id="entity_joint"),
        ],
        cashflows=[
            flow("flow_alex_salary", "Alex salary", FlowType.SALARY_INCOME, CashflowDirection.INFLOW, 120_000,
                 start_year=2020, taxable=True, tax_category=TaxCategory.ORDINARY_INCOME,
                 end_event_id="event_alex_retires"),
            flow("flow_alex_pension", "Alex pension", FlowType.PENSION_INCOME, CashflowDirection.INFLOW, 48_000,
                 annual_growth_rate=0.02, taxable=True, tax_category=TaxCategory.PENSION_INCOME,
                 start_event_id="event_alex_retires"),
            flow("flow_sam_salary", "Sam salary", FlowType.SALARY_INCOME, CashflowDirection.INFLOW, 70_000,
                 start_year=2020, taxable=True, tax_category=TaxCategory.ORDINARY_INCOME,
                 end_event_id="event_sam_retires"),
            flow("flow_sam_pension", "Sam pension", FlowType.PENSION_INCOME, CashflowDirection.INFLOW, 20_000,
                 annual_growth_rate=0.02, taxable=True, tax_category=TaxCategory.PENSION_INCOME,
                 start_event_id="event_sam_retires"),
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
        ],
        scenarios=[
            Scenario(scenario_id="base", name="Base", is_base_case=True),
            Scenario(scenario_id="alex_retires_2028", name="Alex retires Jan 2028",
                     event_overrides={"event_alex_retires": {"year": 2028, "month": 1}}),
            Scenario(scenario_id="higher_inflation", name="Higher inflation (4.5%)",
                     inflation_rate_override=0.045),
            Scenario(scenario_id="sam_retires_65", name="Sam retires at 65",
                     event_overrides={"event_sam_retires": {"age": 65}}),
        ],
        metadata_notes="Fictional example household for learning and testing. Not real data.",
    )


if __name__ == "__main__":
    storage.save_portfolio(build_example_portfolio(), OUT, make_backup=False)
    print(f"Example portfolio written to {OUT}")
