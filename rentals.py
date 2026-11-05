"""
rentals.py

Rental properties built from GROSS operating income instead of one net line,
with a mortgage, so interest, principal and operating expenses are each
tracked and forecast.

1. RentalSpec (+ MortgageSpec) describes a property the way you'd read it off
   a rent roll, the expense ledger and the loan statement.
2. build_rental() turns it into ordinary ManagedFlow records:
     Asset (rental, depreciated)
     Cashflows: gross scheduled rent; vacancy & credit loss; one per expense type
     Loan (secured by the asset) -- payments come from the loan, never a cashflow
   add_rental() adds them to the portfolio.
3. rental_pro_forma() runs a scenario and lays out each year for one rental:

     Gross scheduled rent
   - Vacancy & credit loss
   = Gross operating income (GOI)
   - Operating expenses (property tax, insurance, repairs, management, utilities, other)
   = Net operating income (NOI)
   - Debt service (interest + principal)
   = Cash flow before tax
   Loan balance, debt-service coverage (NOI / debt service), depreciation and
   taxable net (NOI - interest - depreciation).

Amounts are YOUR share in the scenario's ownership view (100% view shows the whole property).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import simulation
from models import (
    Asset, AssetType, Cashflow, CashflowDirection, FlowType, LiabilityType, Loan, Portfolio, Scenario,
    TaxCategory, ValidationError,
)
from scenarios import apply_scenario

# Expense lines a rental can have: key -> (label, FlowType). All deductible against the rent.
EXPENSE_TYPES: Dict[str, Tuple[str, FlowType]] = {
    "property_tax": ("Property tax", FlowType.PROPERTY_TAX),
    "insurance": ("Insurance", FlowType.INSURANCE),
    "repairs": ("Repairs & maintenance", FlowType.REPAIRS),
    "management": ("Property management", FlowType.MANAGEMENT_FEE),
    "utilities": ("Utilities", FlowType.OPERATING_EXPENSE),
    "hoa": ("HOA dues", FlowType.OPERATING_EXPENSE),
    "other": ("Other operating expenses", FlowType.OPERATING_EXPENSE),
}


@dataclass
class MortgageSpec:
    """Enter EITHER the original terms (original_amount + term_years + first payment
    month/year: the balance and payments left are worked out), OR the current
    balance + years_left (from a statement). monthly_payment (principal and
    interest only -- leave escrowed tax/insurance in the expense lines) overrides
    the calculated payment."""
    annual_interest_rate: float
    lender: str = ""
    original_amount: Optional[float] = None
    term_years: Optional[float] = None
    first_payment_year: Optional[int] = None
    first_payment_month: int = 1
    balance: Optional[float] = None
    years_left: Optional[float] = None
    monthly_payment: Optional[float] = None
    notes: str = ""

    @property
    def uses_original_terms(self) -> bool:
        return bool(self.original_amount and self.term_years and self.first_payment_year)


@dataclass
class RentalSpec:
    name: str
    value: float                        # market value, whole property
    start_year: int
    entity_id: Optional[str] = None
    owner: str = ""
    gross_rent: float = 0.0             # gross scheduled rent per YEAR, fully leased
    vacancy_rate: float = 0.0           # 0.05 = 5% of gross rent lost to vacancy/credit
    rent_growth: float = 0.03
    expenses: Dict[str, float] = field(default_factory=dict)   # EXPENSE_TYPES key -> per YEAR
    expense_growth: float = 0.03
    value_growth: float = 0.03
    depreciable_basis: float = 0.0      # building + improvements (not land)
    placed_in_service_year: Optional[int] = None
    recovery_years: float = 27.5
    mortgage: Optional[MortgageSpec] = None
    notes: str = ""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:30] or "rental"


def _unique(base: str, taken: set) -> str:
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}_{n}", n + 1
    return candidate


def build_rental(spec: RentalSpec, portfolio: Portfolio) -> Tuple[Asset, List[Cashflow], Optional[Loan]]:
    """Turn a RentalSpec into an Asset, its Cashflows and its Loan (not yet added)."""
    if not spec.name.strip():
        raise ValidationError("A rental needs a name")
    if spec.gross_rent < 0 or not 0 <= spec.vacancy_rate < 1:
        raise ValidationError(f"'{spec.name}': rent must be >= 0 and vacancy between 0% and 100%")
    unknown = set(spec.expenses) - set(EXPENSE_TYPES)
    if unknown:
        raise ValidationError(f"'{spec.name}': unknown expense type(s) {', '.join(sorted(unknown))}")

    asset_id = portfolio.next_id("asset")
    flow_ids = iter(range(portfolio.next_id("cashflow"), 10**9))
    asset = Asset(
        asset_id=asset_id, name=spec.name, asset_type=AssetType.REAL_ESTATE, owner=spec.owner or spec.name,
        initial_value=spec.value, start_year=spec.start_year, entity_id=spec.entity_id,
        annual_growth_rate=spec.value_growth, tax_category=TaxCategory.RENTAL_INCOME, notes=spec.notes,
        depreciable_basis=spec.depreciable_basis,
        placed_in_service_year=spec.placed_in_service_year if spec.depreciable_basis else None,
        recovery_years=spec.recovery_years if spec.depreciable_basis else 0.0,
    )

    def flow(line, name, ftype, direction, amount, growth, taxable, tax, notes=""):
        return Cashflow(flow_id=next(flow_ids), name=f"{spec.name} - {name}", flow_type=ftype,
                        direction=direction, amount=amount, start_year=spec.start_year, asset_id=asset_id,
                        annual_growth_rate=growth, taxable=taxable, tax_category=tax, notes=notes,
                        line_item=line)

    flows = []
    if spec.gross_rent:
        flows.append(flow("rent", "Gross scheduled rent", FlowType.RENT_INCOME, CashflowDirection.INFLOW,
                          spec.gross_rent, spec.rent_growth, True, TaxCategory.RENTAL_INCOME,
                          "Fully leased, per year"))
    if spec.vacancy_rate:
        flows.append(flow("vacancy", "Vacancy & credit loss", FlowType.OPERATING_EXPENSE, CashflowDirection.OUTFLOW,
                          spec.gross_rent * spec.vacancy_rate, spec.rent_growth, False, TaxCategory.NONE,
                          f"{spec.vacancy_rate:.1%} of gross rent"))
    for key, amount in spec.expenses.items():
        if amount:
            label, ftype = EXPENSE_TYPES[key]
            flows.append(flow(key, label, ftype, CashflowDirection.OUTFLOW, amount, spec.expense_growth, False,
                              TaxCategory.NONE))

    loan = None
    m = spec.mortgage
    if m is not None:
        common = dict(liability_id=portfolio.next_id("liability"),
                      name=f"{spec.name} mortgage", annual_interest_rate=m.annual_interest_rate,
                      liability_type=LiabilityType.MORTGAGE, lender=m.lender, secured_by_asset_id=asset_id,
                      entity_id=spec.entity_id, notes=m.notes)
        if m.uses_original_terms:
            loan = Loan.from_original_terms(
                **common, original_amount=m.original_amount, original_term_months=round(m.term_years * 12),
                origination_year=m.first_payment_year, origination_month=m.first_payment_month,
                as_of_year=spec.start_year, monthly_payment=m.monthly_payment or None)
        elif m.balance:
            if not m.years_left and not m.monthly_payment:
                raise ValidationError(f"'{spec.name}' mortgage: give years left or the monthly payment")
            loan = Loan(**common, balance=m.balance, start_year=spec.start_year,
                        term_months=round(m.years_left * 12) if m.years_left else None,
                        monthly_payment=m.monthly_payment or None)
        else:
            raise ValidationError(f"'{spec.name}' mortgage: give the original terms (amount, years, first "
                                  "payment date) or the current balance and years left")
    return asset, flows, loan


def add_rental(portfolio: Portfolio, spec: RentalSpec) -> Tuple[Asset, List[Cashflow], Optional[Loan]]:
    """Build the records and add them; nothing is added if any part is invalid."""
    asset, flows, loan = build_rental(spec, portfolio)
    portfolio.add_asset(asset)
    try:
        for cf in flows:
            portfolio.add_cashflow(cf)
        if loan is not None:
            portfolio.add_liability(loan)
    except ValidationError:
        portfolio.remove_asset(asset.asset_id)   # also removes its cashflows
        raise
    return asset, flows, loan


def rental_assets(portfolio: Portfolio) -> List[Asset]:
    return [a for a in portfolio.assets if a.tax_category == TaxCategory.RENTAL_INCOME]


def _expense_label(cf: Cashflow) -> str:
    if cf.line_item in EXPENSE_TYPES:
        return EXPENSE_TYPES[cf.line_item][0]
    return cf.name.split(" - ", 1)[-1] if " - " in cf.name else cf.name


def rental_pro_forma(portfolio: Portfolio, scenario: Scenario, asset_id: str,
                     years: Optional[List[int]] = None) -> List[dict]:
    """One row per year for one rental, in the scenario's ownership view."""
    working, _ = apply_scenario(portfolio, scenario)
    asset = working.get_asset(asset_id)
    if asset is None:
        raise ValidationError(f"No asset '{asset_id}'")
    results = simulation.run_simulation(portfolio, scenario).annual_results
    infl = working.assumptions.inflation_rate
    flows = [cf for cf in working.cashflows if cf.asset_id == asset_id]
    loans = {li.liability_id for li in working.liabilities if li.secured_by_asset_id == asset_id}
    rows = []
    for r in results:
        if years is not None and r.year not in years:
            continue
        if r.year < asset.start_year or (asset.sale_year is not None and r.year > asset.sale_year):
            continue
        row = {"Year": r.year, "Gross scheduled rent": 0.0, "Vacancy & credit loss": 0.0}
        other_income = 0.0
        opex: Dict[str, float] = {}
        for cf in flows:
            if not simulation._flow_active_in_year(cf, r.year):
                continue
            amt = cf.annual_amount_in_year(r.year, infl)
            if cf.direction == CashflowDirection.INFLOW:
                if cf.flow_type == FlowType.RENT_INCOME:
                    row["Gross scheduled rent"] += amt
                else:
                    other_income += amt
            elif cf.direction == CashflowDirection.OUTFLOW:
                if cf.line_item == "vacancy":
                    row["Vacancy & credit loss"] += amt
                else:
                    label = _expense_label(cf)
                    opex[label] = opex.get(label, 0.0) + amt
        goi = row["Gross scheduled rent"] - row["Vacancy & credit loss"] + other_income
        if other_income:
            row["Other income"] = other_income
        row["Gross operating income (GOI)"] = goi
        for label in [v[0] for v in EXPENSE_TYPES.values()] + sorted(set(opex) - {v[0] for v in EXPENSE_TYPES.values()}):
            if label in opex:
                row[label] = opex[label]
        total_opex = sum(opex.values())
        row["Total operating expenses"] = total_opex
        noi = goi - total_opex
        row["Net operating income (NOI)"] = noi
        lr = [x for x in r.liability_results if x.liability_id in loans]
        interest = sum(x.interest_paid for x in lr)
        principal = sum(x.principal_paid for x in lr)
        row["Interest"] = interest
        row["Principal"] = principal
        row["Debt service"] = interest + principal
        row["Cash flow before tax"] = noi - interest - principal
        row["Loan balance (end of year)"] = sum(x.ending_balance for x in lr)
        row["Debt coverage (NOI / debt service)"] = (noi / (interest + principal)) if interest + principal else None
        dep = asset.depreciation_in_year(r.year)
        row["Depreciation"] = dep
        row["Taxable net (NOI - interest - depreciation)"] = noi - interest - dep
        ar = next((x for x in r.asset_results if x.asset_id == asset_id), None)
        row["Property value (end of year)"] = ar.ending_value if ar else 0.0
        rows.append(row)
    return rows
