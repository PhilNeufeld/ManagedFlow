"""
models.py

Core data model for the household asset / cashflow / events-and-transitions
planning application.

Design notes
------------
- All persistent objects are plain dataclasses so they serialize to/from
  JSON cleanly and are easy to inspect, diff, and audit.
- Validation is intentionally lightweight in the MVP: enough to catch
  obvious mistakes (bad enum values, negative years, etc.) without turning
  this into a heavyweight schema system. Pydantic could replace this layer
  later without changing the simulation/exports code, since both modules
  only depend on attribute access.
- Money is stored as float (US dollars). For a personal planning tool this
  is acceptable; if cent-level precision ever matters, swap to Decimal.
- Every class that gets persisted has a to_dict()/from_dict() pair. storage.py
  is responsible for writing/reading the JSON file as a whole; these methods
  just handle the per-object shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, List, Dict, Any


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class AssetType(str, Enum):
    REAL_ESTATE = "real_estate"
    ANNUITY = "annuity"
    EQUITY_FUND = "equity_fund"
    BUSINESS = "business"
    TRUST = "trust"
    CASH = "cash"
    PENSION = "pension"
    OTHER = "other"


class LiquidityCategory(str, Enum):
    LIQUID = "liquid"
    SEMI_LIQUID = "semi_liquid"
    ILLIQUID = "illiquid"
    INCOME_ONLY = "income_only"
    RESTRICTED = "restricted"
    DO_NOT_SELL = "do_not_sell"


class TaxCategory(str, Enum):
    ORDINARY_INCOME = "ordinary_income"
    CAPITAL_GAINS = "capital_gains"
    QUALIFIED_DIVIDENDS = "qualified_dividends"
    RENTAL_INCOME = "rental_income"
    PENSION_INCOME = "pension_income"
    ANNUITY_INCOME = "annuity_income"
    BUSINESS_INCOME = "business_income"
    TRUST_INCOME = "trust_income"
    NONE = "none"  # non-taxable flows (e.g. principal repayment, transfers)


class CashflowDirection(str, Enum):
    INFLOW = "inflow"
    OUTFLOW = "outflow"
    TRANSFER = "transfer"
    NONCASH = "noncash"


class Frequency(str, Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    ONE_TIME = "one_time"


class FlowType(str, Enum):
    RENT_INCOME = "rent_income"
    INTEREST_INCOME = "interest_income"
    DIVIDEND_INCOME = "dividend_income"
    PENSION_INCOME = "pension_income"
    ANNUITY_INCOME = "annuity_income"
    BUSINESS_DISTRIBUTION = "business_distribution"
    TRUST_DISTRIBUTION = "trust_distribution"
    SALARY_INCOME = "salary_income"
    PARS_INCOME = "pars_income"
    SOCIAL_SECURITY_INCOME = "social_security_income"
    MORTGAGE_PAYMENT = "mortgage_payment"
    LOAN_PRINCIPAL_REPAYMENT = "loan_principal_repayment"
    LOAN_INTEREST = "loan_interest"
    PROPERTY_TAX = "property_tax"
    INSURANCE = "insurance"
    REPAIRS = "repairs"
    CAPITAL_IMPROVEMENT = "capital_improvement"
    OPERATING_EXPENSE = "operating_expense"
    MANAGEMENT_FEE = "management_fee"
    TAX_PAYMENT = "tax_payment"
    LIVING_EXPENSE = "living_expense"
    PURCHASE_CONTRIBUTION = "purchase_contribution"
    WITHDRAWAL = "withdrawal"
    SALE_PROCEEDS = "sale_proceeds"
    REFINANCE_PROCEEDS = "refinance_proceeds"
    DEBT_PAYOFF = "debt_payoff"
    NONCASH_DEPRECIATION = "noncash_depreciation"
    OTHER = "other"


class EntityType(str, Enum):
    """The legal/tax holding structure a Bundle (or Asset) sits under."""
    PERSONAL = "personal"
    JOINT = "joint"
    LLC = "llc"
    S_CORP = "s_corp"
    C_CORP = "c_corp"
    PARTNERSHIP = "partnership"
    TRUST = "trust"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Currency formatting
# ---------------------------------------------------------------------------

def format_currency(amount: Optional[float]) -> str:
    """Format a dollar amount with a $ sign, thousands separators, no
    decimals (rounded to the nearest dollar), and accounting-style
    parentheses for negatives, e.g. 1234567.0 -> '$1,234,567' and
    -45000.0 -> '($45,000)'.

    This is the single source of truth for currency display used by the
    Excel export, the Streamlit UI, and any other printed output, so the
    whole application reads as consistently formatted dollars.
    """
    if amount is None:
        return ""
    rounded = round(amount)
    if rounded < 0:
        return f"(${abs(rounded):,.0f})"
    return f"${rounded:,.0f}"


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

class ValidationError(ValueError):
    """Raised when a model fails basic structural validation."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _enum_value(value: Any, enum_cls):
    """Accept either an enum member or its raw string value."""
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except ValueError as exc:
        valid = ", ".join(e.value for e in enum_cls)
        raise ValidationError(
            f"Invalid value '{value}' for {enum_cls.__name__}. Valid options: {valid}"
        ) from exc


# ---------------------------------------------------------------------------
# Cashflow
# ---------------------------------------------------------------------------

@dataclass
class Cashflow:
    """A single recurring or one-time cashflow.

    May be attached to an asset (asset_id set) or be a household-level
    flow not tied to any asset (asset_id is None) -- e.g. living expenses,
    pension income, or Fresno State teaching income.
    """

    flow_id: str
    name: str
    flow_type: FlowType
    direction: CashflowDirection
    amount: float
    start_year: int
    asset_id: Optional[str] = None
    end_year: Optional[int] = None
    frequency: Frequency = Frequency.ANNUAL
    annual_growth_rate: float = 0.0
    inflation_adjusted: bool = False
    taxable: bool = False
    tax_category: TaxCategory = TaxCategory.NONE
    cashflow_timing: str = "end_of_year"  # "start_of_year" or "end_of_year"
    affects_asset_value: bool = False
    affects_debt_balance: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        self.flow_type = _enum_value(self.flow_type, FlowType)
        self.direction = _enum_value(self.direction, CashflowDirection)
        self.frequency = _enum_value(self.frequency, Frequency)
        self.tax_category = _enum_value(self.tax_category, TaxCategory)

        _require(bool(self.flow_id), "Cashflow.flow_id is required")
        _require(bool(self.name), "Cashflow.name is required")
        _require(
            self.end_year is None or self.end_year >= self.start_year,
            f"Cashflow '{self.flow_id}': end_year must be >= start_year",
        )
        _require(
            self.cashflow_timing in ("start_of_year", "end_of_year"),
            f"Cashflow '{self.flow_id}': cashflow_timing must be 'start_of_year' or 'end_of_year'",
        )

    def annual_amount_in_year(self, year: int, inflation_rate: float = 0.0) -> float:
        """Return the (sign-aware magnitude, pre-tax) annual cash amount for a
        given year, applying frequency normalization and growth, or 0.0 if
        the flow is not active in that year.

        Two growth effects can stack, compounded independently from
        start_year:

        - annual_growth_rate: a flow-specific growth/decline rate the user
          sets explicitly (e.g. a rental escalating 2%/year regardless of
          the household's global inflation assumption).
        - inflation_rate: the household's global inflation assumption
          (Assumptions.inflation_rate, optionally overridden per scenario),
          applied only when inflation_adjusted=True on this flow.

        Passing inflation_rate=0.0 (the default) reproduces the pre-inflation
        behavior, which is useful for callers that only care about the flow's
        own stated growth.
        """
        if year < self.start_year:
            return 0.0
        if self.end_year is not None and year > self.end_year:
            return 0.0
        if self.frequency == Frequency.ONE_TIME and year != self.start_year:
            return 0.0

        periods_per_year = {
            Frequency.MONTHLY: 12,
            Frequency.QUARTERLY: 4,
            Frequency.ANNUAL: 1,
            Frequency.ONE_TIME: 1,
        }[self.frequency]

        base_annual = self.amount * periods_per_year
        years_elapsed = year - self.start_year
        grown = base_annual * ((1 + self.annual_growth_rate) ** years_elapsed)
        if self.inflation_adjusted:
            grown *= (1 + inflation_rate) ** years_elapsed
        return grown

    def signed_amount_in_year(self, year: int, inflation_rate: float = 0.0) -> float:
        """Same as annual_amount_in_year but negative for outflows."""
        magnitude = self.annual_amount_in_year(year, inflation_rate=inflation_rate)
        if self.direction == CashflowDirection.OUTFLOW:
            return -magnitude
        if self.direction == CashflowDirection.NONCASH:
            return 0.0  # noncash flows (e.g. depreciation) never hit cash totals
        return magnitude

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["flow_type"] = self.flow_type.value
        d["direction"] = self.direction.value
        d["frequency"] = self.frequency.value
        d["tax_category"] = self.tax_category.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Cashflow":
        return cls(**data)


# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------

@dataclass
class Entity:
    """A legal/tax holding structure: a person (personal/joint ownership),
    an LLC, a trust, a corporation, etc. Bundles (and, through them, the
    Assets and Cashflows they contain) are attributed to an Entity, so a
    simulation or report can be scoped to "everything under this LLC" or
    "everything personally owned."
    """

    entity_id: str
    name: str
    entity_type: EntityType = EntityType.PERSONAL
    notes: str = ""

    def __post_init__(self) -> None:
        self.entity_type = _enum_value(self.entity_type, EntityType)
        _require(bool(self.entity_id), "Entity.entity_id is required")
        _require(bool(self.name), "Entity.name is required")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "entity_type": self.entity_type.value,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Entity":
        return cls(**data)


# ---------------------------------------------------------------------------
# Asset
# ---------------------------------------------------------------------------

@dataclass
class Asset:
    """A single household asset (real estate, fund, business, etc.)."""

    asset_id: str
    name: str
    asset_type: AssetType
    owner: str
    initial_value: float
    start_year: int
    entity_id: Optional[str] = None  # FK to Entity.entity_id; holding structure / owner entity
    current_value: Optional[float] = None  # defaults to initial_value if unset
    annual_growth_rate: float = 0.0
    annual_expense_rate: float = 0.0  # as a fraction of value, if used instead of fixed expenses
    liquidity_category: LiquidityCategory = LiquidityCategory.ILLIQUID
    tax_category: TaxCategory = TaxCategory.NONE
    notes: str = ""
    active: bool = True
    do_not_sell: bool = False
    sale_year: Optional[int] = None
    sale_assumptions: Dict[str, Any] = field(default_factory=dict)
    cashflows: List[Cashflow] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.asset_type = _enum_value(self.asset_type, AssetType)
        self.liquidity_category = _enum_value(self.liquidity_category, LiquidityCategory)
        self.tax_category = _enum_value(self.tax_category, TaxCategory)

        if self.current_value is None:
            self.current_value = self.initial_value

        # Normalize embedded cashflows: allow dicts (e.g. fresh from JSON) or
        # Cashflow instances (e.g. constructed in Python/sample_data.py).
        normalized: List[Cashflow] = []
        for cf in self.cashflows:
            normalized.append(cf if isinstance(cf, Cashflow) else Cashflow.from_dict(cf))
        self.cashflows = normalized

        _require(bool(self.asset_id), "Asset.asset_id is required")
        _require(bool(self.name), "Asset.name is required")
        _require(self.initial_value >= 0, f"Asset '{self.asset_id}': initial_value must be >= 0")
        _require(
            self.do_not_sell is False or self.liquidity_category == LiquidityCategory.DO_NOT_SELL
            or True,  # do_not_sell may be set independently of liquidity_category by design
            "",
        )
        if self.liquidity_category == LiquidityCategory.DO_NOT_SELL:
            self.do_not_sell = True

        for cf in self.cashflows:
            _require(
                cf.asset_id in (None, self.asset_id),
                f"Cashflow '{cf.flow_id}' has asset_id '{cf.asset_id}' "
                f"but is attached to asset '{self.asset_id}'",
            )
            cf.asset_id = self.asset_id

    def add_cashflow(self, cashflow: Cashflow) -> None:
        cashflow.asset_id = self.asset_id
        self.cashflows.append(cashflow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "name": self.name,
            "asset_type": self.asset_type.value,
            "owner": self.owner,
            "entity_id": self.entity_id,
            "initial_value": self.initial_value,
            "current_value": self.current_value,
            "start_year": self.start_year,
            "annual_growth_rate": self.annual_growth_rate,
            "annual_expense_rate": self.annual_expense_rate,
            "liquidity_category": self.liquidity_category.value,
            "tax_category": self.tax_category.value,
            "notes": self.notes,
            "active": self.active,
            "do_not_sell": self.do_not_sell,
            "sale_year": self.sale_year,
            "sale_assumptions": self.sale_assumptions,
            "cashflows": [cf.to_dict() for cf in self.cashflows],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Asset":
        data = dict(data)  # don't mutate caller's dict
        return cls(**data)


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------

@dataclass
class Bundle:
    """A named, trackable grouping of related Assets and/or standalone
    (household-level) Cashflows, attributed to an Entity.

    Examples:
    - "Rental Address 1": the rental property Asset (which already carries
      its own loan-payment, appreciation, depreciation, rent-income, and
      expense Cashflows) -- a Bundle with one asset_id and no
      standalone_cashflow_ids.
    - "Person A Earnings": no Asset at all, just a household-level salary
      Cashflow -- a Bundle with no asset_ids and one standalone_cashflow_id.
    - "Household Living Expenses": a household-level expense Cashflow
      shared across people, also asset-free.

    standalone_cashflow_ids holds ids of cashflows in
    Portfolio.household_cashflows -- i.e. cashflows with no asset_id of
    their own. Asset-attached cashflows never need to be listed here:
    they travel automatically with their Asset once that Asset's id is in
    asset_ids.

    A Bundle must reference at least one Asset or one standalone Cashflow
    (an empty Bundle isn't useful). Scenarios can target a specific set of
    Bundles (or all Bundles under an Entity) so a simulation can be scoped
    to e.g. "just this LLC" or "just this rental property."
    """

    bundle_id: str
    name: str
    entity_id: str
    asset_ids: List[str] = field(default_factory=list)
    standalone_cashflow_ids: List[str] = field(default_factory=list)
    notes: str = ""

    def __post_init__(self) -> None:
        _require(bool(self.bundle_id), "Bundle.bundle_id is required")
        _require(bool(self.name), "Bundle.name is required")
        _require(bool(self.entity_id), f"Bundle '{self.bundle_id}': entity_id is required")
        _require(
            bool(self.asset_ids) or bool(self.standalone_cashflow_ids),
            f"Bundle '{self.bundle_id}': must contain at least one asset_id "
            "or standalone_cashflow_id",
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "name": self.name,
            "entity_id": self.entity_id,
            "asset_ids": list(self.asset_ids),
            "standalone_cashflow_ids": list(self.standalone_cashflow_ids),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Bundle":
        return cls(**data)


# ---------------------------------------------------------------------------
# Assumptions
# ---------------------------------------------------------------------------

@dataclass
class Assumptions:
    """Household-wide default assumptions for the simulation horizon."""

    start_year: int
    end_year: int
    inflation_rate: float = 0.03
    default_tax_rates: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require(self.end_year >= self.start_year, "Assumptions.end_year must be >= start_year")
        _require(-0.5 < self.inflation_rate < 1.0, "Assumptions.inflation_rate looks out of range")

        defaults = {
            TaxCategory.ORDINARY_INCOME.value: 0.24,
            TaxCategory.CAPITAL_GAINS.value: 0.15,
            TaxCategory.QUALIFIED_DIVIDENDS.value: 0.15,
            TaxCategory.RENTAL_INCOME.value: 0.24,
            TaxCategory.PENSION_INCOME.value: 0.22,
            TaxCategory.ANNUITY_INCOME.value: 0.22,
            TaxCategory.BUSINESS_INCOME.value: 0.24,
            TaxCategory.TRUST_INCOME.value: 0.24,
        }
        for key, value in defaults.items():
            self.default_tax_rates.setdefault(key, value)

    def tax_rate_for(self, tax_category: "TaxCategory") -> float:
        category = _enum_value(tax_category, TaxCategory)
        if category == TaxCategory.NONE:
            return 0.0
        return self.default_tax_rates.get(category.value, 0.0)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Assumptions":
        return cls(**data)


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

@dataclass
class Scenario:
    """A named set of overrides applied on top of base Assumptions/Assets.

    Overrides are intentionally loose dicts in the MVP so new override types
    can be added without a schema migration. The simulation engine documents
    which keys it understands (see simulation.py: SUPPORTED_OVERRIDE_KEYS).
    """

    scenario_id: str
    name: str
    description: str = ""
    is_base_case: bool = False

    # Global overrides applied to assumptions
    inflation_rate_override: Optional[float] = None
    expense_growth_rate_override: Optional[float] = None

    # Per-asset overrides: {asset_id: {"annual_growth_rate": 0.04, "sale_year": 2032, ...}}
    asset_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Per-cashflow overrides: {flow_id: {"start_year": 2030, "amount": 5000, ...}}
    cashflow_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Withdrawal / reinvestment behavior
    withdrawal_order_override: Optional[List[str]] = None
    allow_illiquid_withdrawals: bool = False
    reinvest_surplus: bool = True

    # Scoping: if either list is non-empty, the simulation is restricted to
    # just the named Entities and/or Bundles instead of the whole portfolio.
    # entity_ids pulls in every Bundle under that Entity; bundle_ids lets you
    # target individual Bundles directly (e.g. just "Rental Address 1"
    # regardless of which Entity it sits under). The two can be combined --
    # the effective set is the union of both. Leave both empty to run the
    # full portfolio (the default, unchanged behavior).
    entity_ids: List[str] = field(default_factory=list)
    bundle_ids: List[str] = field(default_factory=list)

    notes: str = ""

    def __post_init__(self) -> None:
        _require(bool(self.scenario_id), "Scenario.scenario_id is required")
        _require(bool(self.name), "Scenario.name is required")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Scenario":
        return cls(**data)


# ---------------------------------------------------------------------------
# Household
# ---------------------------------------------------------------------------

@dataclass
class Household:
    name: str = ""
    members: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Household":
        return cls(**data)


# ---------------------------------------------------------------------------
# Simulation result containers
# ---------------------------------------------------------------------------

@dataclass
class AnnualAssetResult:
    asset_id: str
    year: int
    beginning_value: float
    growth_amount: float
    net_cashflow_affecting_value: float
    ending_value: float
    gross_income: float = 0.0
    gross_expense: float = 0.0
    sold_this_year: bool = False
    sale_proceeds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AnnualAssetResult":
        return cls(**data)


@dataclass
class TaxEstimate:
    year: int
    taxable_income_by_category: Dict[str, float] = field(default_factory=dict)
    tax_by_category: Dict[str, float] = field(default_factory=dict)
    total_estimated_tax: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaxEstimate":
        return cls(**data)


@dataclass
class AnnualHouseholdResult:
    year: int
    total_gross_income: float = 0.0
    total_expenses: float = 0.0
    total_estimated_tax: float = 0.0
    net_cashflow: float = 0.0
    surplus_or_deficit: float = 0.0
    withdrawal_amount: float = 0.0
    withdrawal_breakdown: Dict[str, float] = field(default_factory=dict)
    reinvestment_amount: float = 0.0
    total_net_worth: float = 0.0
    liquid_net_worth: float = 0.0
    illiquid_net_worth: float = 0.0
    asset_results: List[AnnualAssetResult] = field(default_factory=list)
    tax_estimate: Optional[TaxEstimate] = None
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "year": self.year,
            "total_gross_income": self.total_gross_income,
            "total_expenses": self.total_expenses,
            "total_estimated_tax": self.total_estimated_tax,
            "net_cashflow": self.net_cashflow,
            "surplus_or_deficit": self.surplus_or_deficit,
            "withdrawal_amount": self.withdrawal_amount,
            "withdrawal_breakdown": self.withdrawal_breakdown,
            "reinvestment_amount": self.reinvestment_amount,
            "total_net_worth": self.total_net_worth,
            "liquid_net_worth": self.liquid_net_worth,
            "illiquid_net_worth": self.illiquid_net_worth,
            "asset_results": [a.to_dict() for a in self.asset_results],
            "tax_estimate": self.tax_estimate.to_dict() if self.tax_estimate else None,
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AnnualHouseholdResult":
        data = dict(data)
        data["asset_results"] = [AnnualAssetResult.from_dict(a) for a in data.get("asset_results", [])]
        if data.get("tax_estimate"):
            data["tax_estimate"] = TaxEstimate.from_dict(data["tax_estimate"])
        return cls(**data)


@dataclass
class SimulationResult:
    scenario_id: str
    annual_results: List[AnnualHouseholdResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "annual_results": [r.to_dict() for r in self.annual_results],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SimulationResult":
        return cls(
            scenario_id=data["scenario_id"],
            annual_results=[AnnualHouseholdResult.from_dict(r) for r in data.get("annual_results", [])],
        )


# ---------------------------------------------------------------------------
# Portfolio (top-level container)
# ---------------------------------------------------------------------------

@dataclass
class Portfolio:
    """The full household portfolio: household info, assumptions, assets,
    household-level cashflows, and scenarios. This is what gets persisted
    as portfolio.json (simulation_results are typically persisted separately
    under outputs/, see storage.py).
    """

    household: Household = field(default_factory=Household)
    assumptions: Assumptions = field(
        default_factory=lambda: Assumptions(start_year=2026, end_year=2055)
    )
    entities: List[Entity] = field(default_factory=list)
    assets: List[Asset] = field(default_factory=list)
    household_cashflows: List[Cashflow] = field(default_factory=list)
    bundles: List[Bundle] = field(default_factory=list)
    scenarios: List[Scenario] = field(default_factory=list)
    file_version: str = "0.1"
    created_at: str = ""
    updated_at: str = ""
    metadata_notes: str = ""

    def __post_init__(self) -> None:
        asset_ids = [a.asset_id for a in self.assets]
        duplicates = {a for a in asset_ids if asset_ids.count(a) > 1}
        _require(not duplicates, f"Duplicate asset_id(s) found: {duplicates}")

        flow_ids = [cf.flow_id for cf in self.household_cashflows]
        flow_ids += [cf.flow_id for a in self.assets for cf in a.cashflows]
        dup_flows = {f for f in flow_ids if flow_ids.count(f) > 1}
        _require(not dup_flows, f"Duplicate cashflow flow_id(s) found: {dup_flows}")

        for cf in self.household_cashflows:
            _require(
                cf.asset_id is None,
                f"Household cashflow '{cf.flow_id}' must not have an asset_id "
                f"(found '{cf.asset_id}'); attach it to the asset instead.",
            )

        entity_ids = [e.entity_id for e in self.entities]
        dup_entities = {e for e in entity_ids if entity_ids.count(e) > 1}
        _require(not dup_entities, f"Duplicate entity_id(s) found: {dup_entities}")

        bundle_ids = [b.bundle_id for b in self.bundles]
        dup_bundles = {b for b in bundle_ids if bundle_ids.count(b) > 1}
        _require(not dup_bundles, f"Duplicate bundle_id(s) found: {dup_bundles}")

        entity_id_set = set(entity_ids)
        asset_id_set = set(asset_ids)
        household_flow_id_set = {cf.flow_id for cf in self.household_cashflows}

        for a in self.assets:
            _require(
                a.entity_id is None or a.entity_id in entity_id_set,
                f"Asset '{a.asset_id}' references entity_id '{a.entity_id}' "
                "which is not defined in portfolio.entities",
            )

        for b in self.bundles:
            _require(
                b.entity_id in entity_id_set,
                f"Bundle '{b.bundle_id}' references entity_id '{b.entity_id}' "
                "which is not defined in portfolio.entities",
            )
            for aid in b.asset_ids:
                _require(
                    aid in asset_id_set,
                    f"Bundle '{b.bundle_id}' references asset_id '{aid}' "
                    "which is not defined in portfolio.assets",
                )
            for fid in b.standalone_cashflow_ids:
                _require(
                    fid in household_flow_id_set,
                    f"Bundle '{b.bundle_id}' references standalone_cashflow_id "
                    f"'{fid}' which is not defined in portfolio.household_cashflows",
                )

    def get_asset(self, asset_id: str) -> Optional[Asset]:
        for a in self.assets:
            if a.asset_id == asset_id:
                return a
        return None

    def get_scenario(self, scenario_id: str) -> Optional[Scenario]:
        for s in self.scenarios:
            if s.scenario_id == scenario_id:
                return s
        return None

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        for e in self.entities:
            if e.entity_id == entity_id:
                return e
        return None

    def get_bundle(self, bundle_id: str) -> Optional[Bundle]:
        for b in self.bundles:
            if b.bundle_id == bundle_id:
                return b
        return None

    def bundles_for_entity(self, entity_id: str) -> List[Bundle]:
        return [b for b in self.bundles if b.entity_id == entity_id]

    def resolve_scope(
        self, entity_ids: Optional[List[str]] = None, bundle_ids: Optional[List[str]] = None
    ) -> "tuple[set, set]":
        """Resolve a (entity_ids, bundle_ids) targeting spec -- as found on
        Scenario.entity_ids / Scenario.bundle_ids -- into the concrete set
        of asset_ids and standalone_cashflow_ids it covers.

        The effective scope is the union of: every Bundle under any of the
        named Entities, plus every Bundle named directly in bundle_ids. If
        both entity_ids and bundle_ids are empty, returns (None, None) to
        signal "no scoping, use the whole portfolio" -- callers should
        check for this rather than treating it as an empty scope.
        """
        entity_ids = entity_ids or []
        bundle_ids = bundle_ids or []
        if not entity_ids and not bundle_ids:
            return None, None

        target_bundles: List[Bundle] = []
        for eid in entity_ids:
            target_bundles.extend(self.bundles_for_entity(eid))
        for bid in bundle_ids:
            b = self.get_bundle(bid)
            if b is not None and b not in target_bundles:
                target_bundles.append(b)

        asset_id_scope = set()
        flow_id_scope = set()
        for b in target_bundles:
            asset_id_scope.update(b.asset_ids)
            flow_id_scope.update(b.standalone_cashflow_ids)
        return asset_id_scope, flow_id_scope

    def all_cashflows(self) -> List[Cashflow]:
        """All cashflows in the portfolio: household-level plus every
        asset's attached cashflows."""
        flows = list(self.household_cashflows)
        for asset in self.assets:
            flows.extend(asset.cashflows)
        return flows

    def get_cashflow(self, flow_id: str) -> Optional[Cashflow]:
        for cf in self.all_cashflows():
            if cf.flow_id == flow_id:
                return cf
        return None

    def replace_cashflow(self, flow_id: str, new_cashflow: Cashflow) -> None:
        """Replace the cashflow with id `flow_id` (wherever it lives --
        household-level or attached to an Asset) with `new_cashflow`,
        in place. Used by the UI's "save edits to an existing cashflow"
        flow. Raises ValidationError if no cashflow with that id exists.
        """
        for i, cf in enumerate(self.household_cashflows):
            if cf.flow_id == flow_id:
                self.household_cashflows[i] = new_cashflow
                return
        for asset in self.assets:
            for i, cf in enumerate(asset.cashflows):
                if cf.flow_id == flow_id:
                    asset.cashflows[i] = new_cashflow
                    return
        _require(False, f"No cashflow with id '{flow_id}' found to replace")

    def add_asset(self, asset: Asset) -> None:
        _require(
            self.get_asset(asset.asset_id) is None,
            f"Asset with id '{asset.asset_id}' already exists",
        )
        self.assets.append(asset)

    def replace_asset(self, asset_id: str, new_asset: Asset) -> None:
        for i, a in enumerate(self.assets):
            if a.asset_id == asset_id:
                self.assets[i] = new_asset
                return
        _require(False, f"No asset with id '{asset_id}' found to replace")

    def add_household_cashflow(self, cashflow: Cashflow) -> None:
        _require(
            cashflow.asset_id is None,
            f"Household cashflow '{cashflow.flow_id}' must not reference an asset_id",
        )
        self.household_cashflows.append(cashflow)

    def add_scenario(self, scenario: Scenario) -> None:
        _require(
            self.get_scenario(scenario.scenario_id) is None,
            f"Scenario with id '{scenario.scenario_id}' already exists",
        )
        self.scenarios.append(scenario)

    def replace_scenario(self, scenario_id: str, new_scenario: Scenario) -> None:
        for i, s in enumerate(self.scenarios):
            if s.scenario_id == scenario_id:
                self.scenarios[i] = new_scenario
                return
        _require(False, f"No scenario with id '{scenario_id}' found to replace")

    def add_entity(self, entity: Entity) -> None:
        _require(
            self.get_entity(entity.entity_id) is None,
            f"Entity with id '{entity.entity_id}' already exists",
        )
        self.entities.append(entity)

    def replace_entity(self, entity_id: str, new_entity: Entity) -> None:
        for i, e in enumerate(self.entities):
            if e.entity_id == entity_id:
                self.entities[i] = new_entity
                return
        _require(False, f"No entity with id '{entity_id}' found to replace")

    def add_bundle(self, bundle: Bundle) -> None:
        _require(
            self.get_bundle(bundle.bundle_id) is None,
            f"Bundle with id '{bundle.bundle_id}' already exists",
        )
        _require(
            self.get_entity(bundle.entity_id) is not None,
            f"Bundle '{bundle.bundle_id}' references unknown entity_id '{bundle.entity_id}'",
        )
        self.bundles.append(bundle)

    def replace_bundle(self, bundle_id: str, new_bundle: Bundle) -> None:
        for i, b in enumerate(self.bundles):
            if b.bundle_id == bundle_id:
                self.bundles[i] = new_bundle
                return
        _require(False, f"No bundle with id '{bundle_id}' found to replace")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metadata": {
                "file_version": self.file_version,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "notes": self.metadata_notes,
            },
            "household": self.household.to_dict(),
            "assumptions": self.assumptions.to_dict(),
            "entities": [e.to_dict() for e in self.entities],
            "assets": [a.to_dict() for a in self.assets],
            "household_cashflows": [cf.to_dict() for cf in self.household_cashflows],
            "bundles": [b.to_dict() for b in self.bundles],
            "scenarios": [s.to_dict() for s in self.scenarios],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Portfolio":
        metadata = data.get("metadata", {})
        return cls(
            household=Household.from_dict(data.get("household", {})),
            assumptions=Assumptions.from_dict(data["assumptions"]),
            entities=[Entity.from_dict(e) for e in data.get("entities", [])],
            assets=[Asset.from_dict(a) for a in data.get("assets", [])],
            household_cashflows=[Cashflow.from_dict(cf) for cf in data.get("household_cashflows", [])],
            bundles=[Bundle.from_dict(b) for b in data.get("bundles", [])],
            scenarios=[Scenario.from_dict(s) for s in data.get("scenarios", [])],
            file_version=metadata.get("file_version", "0.1"),
            created_at=metadata.get("created_at", ""),
            updated_at=metadata.get("updated_at", ""),
            metadata_notes=metadata.get("notes", ""),
        )
