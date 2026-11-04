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
from typing import Optional, List, Dict, Any, ClassVar, Tuple

from migrations import CURRENT_SCHEMA_VERSION, migrate


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


class LiabilityType(str, Enum):
    """What kind of debt a Liability is (a label; the math lives in the
    Loan / CreditLine classes)."""
    MORTGAGE = "mortgage"
    HELOC = "heloc"
    FARM = "farm"
    BUSINESS = "business"
    CAR = "car"
    STUDENT = "student"
    LINE_OF_CREDIT = "line_of_credit"
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
    # Partial years: the flow runs from start_month of start_year through
    # end_month of end_year, and those years are prorated by month.
    start_month: int = 1
    end_month: int = 12
    # Optional links to Events; when set, they decide the dates above.
    start_event_id: Optional[str] = None
    end_event_id: Optional[str] = None

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
        self.check_dates()

    def check_dates(self) -> None:
        _require(1 <= self.start_month <= 12 and 1 <= self.end_month <= 12,
                 f"Cashflow '{self.flow_id}': months must be 1-12")
        _require(
            self.end_year is None or (self.end_year, self.end_month) >= (self.start_year, self.start_month),
            f"Cashflow '{self.flow_id}': ends ({self.end_month}/{self.end_year}) before it starts "
            f"({self.start_month}/{self.start_year})",
        )

    def months_active_in_year(self, year: int) -> int:
        """How many months of `year` this flow runs (0-12)."""
        if year < self.start_year or (self.end_year is not None and year > self.end_year):
            return 0
        first = self.start_month if year == self.start_year else 1
        last = self.end_month if (self.end_year is not None and year == self.end_year) else 12
        return max(0, last - first + 1)

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
        if self.frequency != Frequency.ONE_TIME:
            grown *= self.months_active_in_year(year) / 12  # partial first/last year
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
# Event
# ---------------------------------------------------------------------------

def month_before(year: int, month: int) -> Tuple[int, int]:
    """(2026, 7) -> (2026, 6);  (2026, 1) -> (2025, 12)."""
    return (year, month - 1) if month > 1 else (year - 1, 12)


@dataclass
class Event:
    """A named moment that other records can be tied to: Retirement,
    Debbie starts CalPERS, sell 2049 Broadway, Brix loan payoff.

    (year, month) is the FIRST month things are different. Records linked
    with start_event_id begin that month; records linked with end_event_id
    stop the month before. So with Retirement = July 2026, salary (ends at
    Retirement) runs through June and the pension (starts at Retirement)
    begins in July -- no double-counted month.

    Move the event and everything linked to it moves. A scenario can move
    it too: Scenario.event_overrides = {"event_retirement": {"year": 2028}}.
    """

    event_id: str
    name: str
    year: int
    month: int = 1
    notes: str = ""

    def __post_init__(self) -> None:
        _require(bool(self.event_id), "Event.event_id is required")
        _require(bool(self.name), "Event.name is required")
        _require(1 <= self.month <= 12, f"Event '{self.event_id}': month must be 1-12")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
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
    # Optional Events: start_event_id sets start_year (purchase);
    # end_event_id sets sale_year (sold in that event's year).
    start_event_id: Optional[str] = None
    end_event_id: Optional[str] = None
    # Cashflows are NOT stored on the asset (schema v0.2). They live in
    # Portfolio.cashflows and point here via Cashflow.asset_id. Use
    # Portfolio.cashflows_for_asset(asset_id) to get them.

    def __post_init__(self) -> None:
        self.asset_type = _enum_value(self.asset_type, AssetType)
        self.liquidity_category = _enum_value(self.liquidity_category, LiquidityCategory)
        self.tax_category = _enum_value(self.tax_category, TaxCategory)

        if self.current_value is None:
            self.current_value = self.initial_value

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
            "start_event_id": self.start_event_id,
            "end_event_id": self.end_event_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Asset":
        data = dict(data)  # don't mutate caller's dict
        return cls(**data)


# ---------------------------------------------------------------------------
# Liabilities: Liability (base) -> Loan, CreditLine
# ---------------------------------------------------------------------------

def amortized_monthly_payment(principal: float, annual_rate: float, months: int) -> float:
    """The fixed monthly payment that pays off `principal` in `months`
    months at `annual_rate` (0.06 = 6%), interest compounded monthly.

    Standard formula:  P = B * r / (1 - (1 + r) ** -n),  with r = annual_rate / 12.
    Example: $100,000 at 6% for 360 months -> $599.55.
    """
    _require(months > 0, "Loan term must be at least 1 month")
    r = annual_rate / 12.0
    if r == 0:
        return principal / months
    return principal * r / (1 - (1 + r) ** -months)


@dataclass
class Liability:
    """Money owed. This is the BASE class: it holds what every debt has in
    common. Subclasses add the rules for how a specific kind of debt is paid
    down each year by overriding year_activity():

    - Loan:       fixed monthly payment that amortizes the balance to zero.
    - CreditLine: interest-only (HELOC, line of credit); balance stays put.

    balance is the amount owed at the START of start_year. A liability that
    starts in a future year (e.g. a mortgage on a property bought in 2028)
    owes nothing before then.

    secured_by_asset_id links a debt to its collateral (a mortgage to its
    house). Secured debts travel with their asset into scoped simulations,
    and are paid off in full the year that asset is sold.
    """

    liability_id: str
    name: str
    balance: float
    annual_interest_rate: float
    start_year: int
    liability_type: LiabilityType = LiabilityType.OTHER
    entity_id: Optional[str] = None
    secured_by_asset_id: Optional[str] = None
    lender: str = ""
    notes: str = ""
    active: bool = True
    # Pay the remaining balance in full in this year (refinance, payoff).
    payoff_year: Optional[int] = None
    # Optional Events: start_event_id sets start_year; end_event_id sets payoff_year.
    start_event_id: Optional[str] = None
    end_event_id: Optional[str] = None

    # ClassVar = belongs to the class, not stored per-object or in __init__.
    # Saved in JSON as "kind" so from_dict knows which subclass to rebuild.
    kind: ClassVar[str] = "liability"

    def __post_init__(self) -> None:
        self.liability_type = _enum_value(self.liability_type, LiabilityType)
        _require(bool(self.liability_id), "Liability.liability_id is required")
        _require(bool(self.name), "Liability.name is required")
        _require(self.balance >= 0, f"Liability '{self.liability_id}': balance must be >= 0")
        _require(
            0 <= self.annual_interest_rate < 1,
            f"Liability '{self.liability_id}': annual_interest_rate is a fraction "
            f"(0.06 for 6%), got {self.annual_interest_rate}",
        )

    def year_activity(self, beginning_balance: float) -> Tuple[float, float, float]:
        """Return (interest_paid, principal_paid, ending_balance) for one
        year that starts at beginning_balance. Each subclass defines this."""
        raise NotImplementedError

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "liability_id": self.liability_id,
            "name": self.name,
            "liability_type": self.liability_type.value,
            "balance": self.balance,
            "annual_interest_rate": self.annual_interest_rate,
            "start_year": self.start_year,
            "entity_id": self.entity_id,
            "secured_by_asset_id": self.secured_by_asset_id,
            "lender": self.lender,
            "notes": self.notes,
            "active": self.active,
            "payoff_year": self.payoff_year,
            "start_event_id": self.start_event_id,
            "end_event_id": self.end_event_id,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Liability":
        return liability_from_dict(data)


@dataclass
class Loan(Liability):
    """An amortizing loan: mortgage, farm loan, car loan, student loan.

    Give either term_months (months left to pay as of start_year) and let the
    app compute the payment, or monthly_payment (what the lender bills, e.g.
    from a statement). If both are given, monthly_payment wins.
    """

    term_months: Optional[int] = None
    monthly_payment: Optional[float] = None

    kind: ClassVar[str] = "loan"

    def __post_init__(self) -> None:
        super().__post_init__()  # run the base-class checks first
        _require(
            self.term_months is not None or self.monthly_payment is not None,
            f"Loan '{self.liability_id}': give term_months or monthly_payment",
        )
        if self.term_months is not None:
            _require(self.term_months > 0, f"Loan '{self.liability_id}': term_months must be > 0")
        first_month_interest = self.balance * self.annual_interest_rate / 12
        _require(
            self.balance == 0 or self.scheduled_monthly_payment() > first_month_interest,
            f"Loan '{self.liability_id}': a ${self.scheduled_monthly_payment():,.2f} payment doesn't "
            f"cover the ${first_month_interest:,.2f} monthly interest, so the balance would grow. "
            "Use a CreditLine for interest-only debt.",
        )

    def scheduled_monthly_payment(self) -> float:
        if self.monthly_payment is not None:
            return self.monthly_payment
        return amortized_monthly_payment(self.balance, self.annual_interest_rate, self.term_months)

    def year_activity(self, beginning_balance: float) -> Tuple[float, float, float]:
        """Walk 12 monthly payments. Each month: interest = balance x rate/12;
        the rest of the payment reduces the balance. Stops at payoff."""
        monthly_rate = self.annual_interest_rate / 12
        payment = self.scheduled_monthly_payment()
        balance = beginning_balance
        interest_paid = 0.0
        principal_paid = 0.0
        for _month in range(12):
            if balance <= 0.005:  # paid off (allow for rounding pennies)
                balance = 0.0
                break
            interest = balance * monthly_rate
            principal = min(payment - interest, balance)
            interest_paid += interest
            principal_paid += principal
            balance -= principal
        return interest_paid, principal_paid, max(balance, 0.0)

    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data.update({"term_months": self.term_months, "monthly_payment": self.monthly_payment})
        return data


@dataclass
class CreditLine(Liability):
    """Interest-only revolving debt: HELOC, personal line of credit.
    Each year you pay the interest; the balance stays the same."""

    credit_limit: Optional[float] = None

    kind: ClassVar[str] = "credit_line"

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.credit_limit is not None:
            _require(
                self.balance <= self.credit_limit,
                f"Credit line '{self.liability_id}': balance ${self.balance:,.0f} is over "
                f"the ${self.credit_limit:,.0f} limit",
            )

    def year_activity(self, beginning_balance: float) -> Tuple[float, float, float]:
        return beginning_balance * self.annual_interest_rate, 0.0, beginning_balance

    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data["credit_limit"] = self.credit_limit
        return data


# "kind" stored in JSON -> class to rebuild. Add new subclasses here.
LIABILITY_KINDS: Dict[str, type] = {"loan": Loan, "credit_line": CreditLine}


def liability_from_dict(data: Dict[str, Any]) -> Liability:
    data = dict(data)
    kind = data.pop("kind", "loan")
    _require(kind in LIABILITY_KINDS, f"Unknown liability kind '{kind}'")
    return LIABILITY_KINDS[kind](**data)


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------

@dataclass
class Bundle:
    """A named grouping of related Assets and Cashflows, attributed to an
    Entity -- e.g. "4392 Brix": the house plus its rent and expenses, or
    "Phil earnings": just a salary cashflow.

    - asset_ids: assets in the bundle. Every cashflow linked to one of these
      assets (Cashflow.asset_id) comes along automatically.
    - cashflow_ids: household-level cashflows (asset_id None) that belong to
      the bundle, e.g. the salary in "Phil earnings".
    - liability_ids: debts in the bundle. A debt secured by one of the
      bundle's assets comes along automatically, like its cashflows.

    Schema v0.1 called cashflow_ids "standalone_cashflow_ids";
    migrations.py renames it when an old file is loaded.

    A Bundle must reference at least one asset or cashflow. Scenarios can
    target Bundles (or every Bundle under an Entity) to scope a simulation
    to e.g. "just the rental LLC" or "just this property."
    """

    bundle_id: str
    name: str
    entity_id: str
    asset_ids: List[str] = field(default_factory=list)
    cashflow_ids: List[str] = field(default_factory=list)
    liability_ids: List[str] = field(default_factory=list)
    notes: str = ""

    def __post_init__(self) -> None:
        _require(bool(self.bundle_id), "Bundle.bundle_id is required")
        _require(bool(self.name), "Bundle.name is required")
        _require(bool(self.entity_id), f"Bundle '{self.bundle_id}': entity_id is required")
        _require(
            bool(self.asset_ids) or bool(self.cashflow_ids) or bool(self.liability_ids),
            f"Bundle '{self.bundle_id}': must contain at least one asset, cashflow, or liability",
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "name": self.name,
            "entity_id": self.entity_id,
            "asset_ids": list(self.asset_ids),
            "cashflow_ids": list(self.cashflow_ids),
            "liability_ids": list(self.liability_ids),
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

    # Move events: {event_id: {"year": 2028, "month": 7}}. Everything linked
    # to the event moves with it.
    event_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)

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
class AnnualLiabilityResult:
    liability_id: str
    year: int
    beginning_balance: float = 0.0
    interest_paid: float = 0.0
    principal_paid: float = 0.0
    ending_balance: float = 0.0
    paid_off_this_year: bool = False

    @property
    def debt_service(self) -> float:
        return self.interest_paid + self.principal_paid

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AnnualLiabilityResult":
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
    total_assets: float = 0.0
    total_liabilities: float = 0.0
    total_debt_service: float = 0.0
    total_interest_paid: float = 0.0
    asset_results: List[AnnualAssetResult] = field(default_factory=list)
    liability_results: List[AnnualLiabilityResult] = field(default_factory=list)
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
            "total_assets": self.total_assets,
            "total_liabilities": self.total_liabilities,
            "total_debt_service": self.total_debt_service,
            "total_interest_paid": self.total_interest_paid,
            "asset_results": [a.to_dict() for a in self.asset_results],
            "liability_results": [r.to_dict() for r in self.liability_results],
            "tax_estimate": self.tax_estimate.to_dict() if self.tax_estimate else None,
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AnnualHouseholdResult":
        data = dict(data)
        data["asset_results"] = [AnnualAssetResult.from_dict(a) for a in data.get("asset_results", [])]
        data["liability_results"] = [
            AnnualLiabilityResult.from_dict(r) for r in data.get("liability_results", [])
        ]
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
    """The full household portfolio: household info, assumptions, entities,
    assets, cashflows, bundles, and scenarios. This is what gets persisted
    as portfolio.json.

    Schema v0.2: every cashflow lives in ONE list, `cashflows`. A cashflow
    belongs to an asset when its asset_id is set (rent on a rental), or is
    household-level when asset_id is None (salary, Social Security, living
    expenses). Older files are upgraded automatically by migrations.py.
    """

    household: Household = field(default_factory=Household)
    assumptions: Assumptions = field(
        default_factory=lambda: Assumptions(start_year=2026, end_year=2055)
    )
    entities: List[Entity] = field(default_factory=list)
    assets: List[Asset] = field(default_factory=list)
    cashflows: List[Cashflow] = field(default_factory=list)
    liabilities: List[Liability] = field(default_factory=list)
    events: List[Event] = field(default_factory=list)
    bundles: List[Bundle] = field(default_factory=list)
    scenarios: List[Scenario] = field(default_factory=list)
    file_version: str = CURRENT_SCHEMA_VERSION
    created_at: str = ""
    updated_at: str = ""
    metadata_notes: str = ""

    def __post_init__(self) -> None:
        asset_ids = [a.asset_id for a in self.assets]
        duplicates = {a for a in asset_ids if asset_ids.count(a) > 1}
        _require(not duplicates, f"Duplicate asset_id(s) found: {duplicates}")

        flow_ids = [cf.flow_id for cf in self.cashflows]
        dup_flows = {f for f in flow_ids if flow_ids.count(f) > 1}
        _require(not dup_flows, f"Duplicate cashflow flow_id(s) found: {dup_flows}")

        entity_ids = [e.entity_id for e in self.entities]
        dup_entities = {e for e in entity_ids if entity_ids.count(e) > 1}
        _require(not dup_entities, f"Duplicate entity_id(s) found: {dup_entities}")

        liability_ids = [li.liability_id for li in self.liabilities]
        dup_liabilities = {x for x in liability_ids if liability_ids.count(x) > 1}
        _require(not dup_liabilities, f"Duplicate liability_id(s) found: {dup_liabilities}")

        event_ids = [ev.event_id for ev in self.events]
        dup_events = {x for x in event_ids if event_ids.count(x) > 1}
        _require(not dup_events, f"Duplicate event_id(s) found: {dup_events}")
        event_id_set = set(event_ids)
        for record_id, link in self._event_links():
            _require(link in event_id_set, f"'{record_id}' is linked to event '{link}' which doesn't exist")

        bundle_ids = [b.bundle_id for b in self.bundles]
        dup_bundles = {b for b in bundle_ids if bundle_ids.count(b) > 1}
        _require(not dup_bundles, f"Duplicate bundle_id(s) found: {dup_bundles}")

        entity_id_set = set(entity_ids)
        asset_id_set = set(asset_ids)
        flow_id_set = set(flow_ids)

        # Referential integrity: every id that points somewhere must point
        # at something that exists.
        for cf in self.cashflows:
            _require(
                cf.asset_id is None or cf.asset_id in asset_id_set,
                f"Cashflow '{cf.flow_id}' references asset_id '{cf.asset_id}' "
                "which is not defined in portfolio.assets",
            )

        for li in self.liabilities:
            _require(
                li.entity_id is None or li.entity_id in entity_id_set,
                f"Liability '{li.liability_id}' references entity_id '{li.entity_id}' "
                "which is not defined in portfolio.entities",
            )
            _require(
                li.secured_by_asset_id is None or li.secured_by_asset_id in asset_id_set,
                f"Liability '{li.liability_id}' is secured by asset_id '{li.secured_by_asset_id}' "
                "which is not defined in portfolio.assets",
            )

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
            for fid in b.cashflow_ids:
                _require(
                    fid in flow_id_set,
                    f"Bundle '{b.bundle_id}' references cashflow_id '{fid}' "
                    "which is not defined in portfolio.cashflows",
                )
            for lid in b.liability_ids:
                _require(
                    lid in set(liability_ids),
                    f"Bundle '{b.bundle_id}' references liability_id '{lid}' "
                    "which is not defined in portfolio.liabilities",
                )

        self.apply_event_dates()

    # -- events ------------------------------------------------------------------

    def _event_links(self):
        """Yield (record_id, event_id) for every event link in the portfolio."""
        for rec_id, rec in (
            [(cf.flow_id, cf) for cf in self.cashflows]
            + [(a.asset_id, a) for a in self.assets]
            + [(li.liability_id, li) for li in self.liabilities]
        ):
            for link in (rec.start_event_id, rec.end_event_id):
                if link:
                    yield rec_id, link

    def get_event(self, event_id: str) -> Optional[Event]:
        for ev in self.events:
            if ev.event_id == event_id:
                return ev
        return None

    def records_linked_to_event(self, event_id: str) -> List[str]:
        return [rec_id for rec_id, link in self._event_links() if link == event_id]

    def apply_event_dates(self) -> None:
        """Copy each linked Event's date onto the records that point at it.
        Called on load, after editing an event, and inside every scenario run
        (after the scenario has moved any events)."""
        events = {ev.event_id: ev for ev in self.events}
        for cf in self.cashflows:
            if cf.start_event_id in events:
                ev = events[cf.start_event_id]
                cf.start_year, cf.start_month = ev.year, ev.month
            if cf.end_event_id in events:
                ev = events[cf.end_event_id]
                cf.end_year, cf.end_month = month_before(ev.year, ev.month)
            cf.check_dates()
        for a in self.assets:
            if a.start_event_id in events:
                a.start_year = events[a.start_event_id].year
            if a.end_event_id in events:
                a.sale_year = events[a.end_event_id].year
        for li in self.liabilities:
            if li.start_event_id in events:
                li.start_year = events[li.start_event_id].year
            if li.end_event_id in events:
                li.payoff_year = events[li.end_event_id].year

    def add_event(self, event: Event) -> None:
        _require(self.get_event(event.event_id) is None, f"Event with id '{event.event_id}' already exists")
        self.events.append(event)

    def replace_event(self, event_id: str, new_event: Event) -> None:
        for i, ev in enumerate(self.events):
            if ev.event_id == event_id:
                self.events[i] = new_event
                self.apply_event_dates()  # move everything linked to it
                return
        _require(False, f"No event with id '{event_id}' found to replace")

    def remove_event(self, event_id: str) -> None:
        """Remove an event. Linked records keep their current dates but are
        un-linked."""
        _require(self.get_event(event_id) is not None, f"No event with id '{event_id}' found to remove")
        for rec in [*self.cashflows, *self.assets, *self.liabilities]:
            if rec.start_event_id == event_id:
                rec.start_event_id = None
            if rec.end_event_id == event_id:
                rec.end_event_id = None
        self.events = [ev for ev in self.events if ev.event_id != event_id]

    # -- lookups -------------------------------------------------------------

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
    ) -> "tuple[set, set, set]":
        """Resolve a (entity_ids, bundle_ids) targeting spec -- as found on
        Scenario.entity_ids / Scenario.bundle_ids -- into the concrete sets
        of (asset_ids, household-level cashflow_ids, liability_ids) it covers.

        The effective scope is the union of: every Bundle under any of the
        named Entities, plus every Bundle named directly in bundle_ids. If
        both are empty, returns (None, None, None) to signal "no scoping, use the
        whole portfolio" -- callers should check for this rather than
        treating it as an empty scope.
        """
        entity_ids = entity_ids or []
        bundle_ids = bundle_ids or []
        if not entity_ids and not bundle_ids:
            return None, None, None

        target_bundles: List[Bundle] = []
        for eid in entity_ids:
            target_bundles.extend(self.bundles_for_entity(eid))
        for bid in bundle_ids:
            b = self.get_bundle(bid)
            if b is not None and b not in target_bundles:
                target_bundles.append(b)

        asset_id_scope = set()
        flow_id_scope = set()
        liability_id_scope = set()
        for b in target_bundles:
            asset_id_scope.update(b.asset_ids)
            flow_id_scope.update(b.cashflow_ids)
            liability_id_scope.update(b.liability_ids)
        return asset_id_scope, flow_id_scope, liability_id_scope

    # -- cashflows -------------------------------------------------------------

    def all_cashflows(self) -> List[Cashflow]:
        """Every cashflow in the portfolio (a new list, safe to modify)."""
        return list(self.cashflows)

    def cashflows_for_asset(self, asset_id: str) -> List[Cashflow]:
        """Cashflows linked to one asset, in the order they were entered."""
        return [cf for cf in self.cashflows if cf.asset_id == asset_id]

    def household_level_cashflows(self) -> List[Cashflow]:
        """Cashflows not linked to any asset: salary, Social Security,
        living expenses, and so on."""
        return [cf for cf in self.cashflows if cf.asset_id is None]

    def get_cashflow(self, flow_id: str) -> Optional[Cashflow]:
        for cf in self.cashflows:
            if cf.flow_id == flow_id:
                return cf
        return None

    def _require_known_asset(self, cashflow: Cashflow) -> None:
        _require(
            cashflow.asset_id is None or self.get_asset(cashflow.asset_id) is not None,
            f"Cashflow '{cashflow.flow_id}' references unknown asset_id '{cashflow.asset_id}'",
        )
        self._require_known_events(cashflow)

    def _require_known_events(self, record) -> None:
        for link in (record.start_event_id, record.end_event_id):
            _require(link is None or self.get_event(link) is not None, f"Unknown event '{link}'")

    def add_cashflow(self, cashflow: Cashflow) -> None:
        _require(
            self.get_cashflow(cashflow.flow_id) is None,
            f"Cashflow with id '{cashflow.flow_id}' already exists",
        )
        self._require_known_asset(cashflow)
        self.cashflows.append(cashflow)
        self.apply_event_dates()

    def replace_cashflow(self, flow_id: str, new_cashflow: Cashflow) -> None:
        """Replace the cashflow with id `flow_id` in place. Moving a flow to
        a different asset (or to household-level) is just a change to its
        asset_id -- there is no separate container to move it between."""
        self._require_known_asset(new_cashflow)
        for i, cf in enumerate(self.cashflows):
            if cf.flow_id == flow_id:
                self.cashflows[i] = new_cashflow
                self.apply_event_dates()
                return
        _require(False, f"No cashflow with id '{flow_id}' found to replace")

    def remove_cashflow(self, flow_id: str) -> None:
        _require(self.get_cashflow(flow_id) is not None, f"No cashflow with id '{flow_id}' found to remove")
        self.cashflows = [cf for cf in self.cashflows if cf.flow_id != flow_id]

    # -- assets ----------------------------------------------------------------

    def add_asset(self, asset: Asset) -> None:
        _require(
            self.get_asset(asset.asset_id) is None,
            f"Asset with id '{asset.asset_id}' already exists",
        )
        self._require_known_events(asset)
        self.assets.append(asset)
        self.apply_event_dates()

    def replace_asset(self, asset_id: str, new_asset: Asset) -> None:
        for i, a in enumerate(self.assets):
            if a.asset_id == asset_id:
                self._require_known_events(new_asset)
                self.assets[i] = new_asset
                self.apply_event_dates()
                return
        _require(False, f"No asset with id '{asset_id}' found to replace")

    def remove_asset(self, asset_id: str) -> List[Cashflow]:
        """Remove an asset AND the cashflows linked to it (a rent flow means
        nothing without its property). Returns the removed cashflows.

        Debts secured by the asset are NOT deleted -- removing a record
        doesn't make a loan go away -- they're just un-linked."""
        _require(self.get_asset(asset_id) is not None, f"No asset with id '{asset_id}' found to remove")
        for li in self.liabilities_secured_by(asset_id):
            li.secured_by_asset_id = None
        removed = self.cashflows_for_asset(asset_id)
        self.assets = [a for a in self.assets if a.asset_id != asset_id]
        self.cashflows = [cf for cf in self.cashflows if cf.asset_id != asset_id]
        return removed

    # -- liabilities -----------------------------------------------------------

    def get_liability(self, liability_id: str) -> Optional[Liability]:
        for li in self.liabilities:
            if li.liability_id == liability_id:
                return li
        return None

    def liabilities_secured_by(self, asset_id: str) -> List[Liability]:
        return [li for li in self.liabilities if li.secured_by_asset_id == asset_id]

    def _require_valid_liability_links(self, liability: Liability) -> None:
        _require(
            liability.secured_by_asset_id is None or self.get_asset(liability.secured_by_asset_id) is not None,
            f"Liability '{liability.liability_id}' is secured by unknown asset '{liability.secured_by_asset_id}'",
        )
        _require(
            liability.entity_id is None or self.get_entity(liability.entity_id) is not None,
            f"Liability '{liability.liability_id}' references unknown entity '{liability.entity_id}'",
        )
        self._require_known_events(liability)

    def add_liability(self, liability: Liability) -> None:
        _require(
            self.get_liability(liability.liability_id) is None,
            f"Liability with id '{liability.liability_id}' already exists",
        )
        self._require_valid_liability_links(liability)
        self.liabilities.append(liability)
        self.apply_event_dates()

    def replace_liability(self, liability_id: str, new_liability: Liability) -> None:
        self._require_valid_liability_links(new_liability)
        for i, li in enumerate(self.liabilities):
            if li.liability_id == liability_id:
                self.liabilities[i] = new_liability
                self.apply_event_dates()
                return
        _require(False, f"No liability with id '{liability_id}' found to replace")

    def remove_liability(self, liability_id: str) -> None:
        _require(self.get_liability(liability_id) is not None, f"No liability with id '{liability_id}' found to remove")
        self.liabilities = [li for li in self.liabilities if li.liability_id != liability_id]

    # -- scenarios, entities, bundles -----------------------------------------

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

    # -- JSON round trip -------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metadata": {
                "file_version": CURRENT_SCHEMA_VERSION,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "notes": self.metadata_notes,
            },
            "household": self.household.to_dict(),
            "assumptions": self.assumptions.to_dict(),
            "entities": [e.to_dict() for e in self.entities],
            "assets": [a.to_dict() for a in self.assets],
            "cashflows": [cf.to_dict() for cf in self.cashflows],
            "liabilities": [li.to_dict() for li in self.liabilities],
            "events": [ev.to_dict() for ev in self.events],
            "bundles": [b.to_dict() for b in self.bundles],
            "scenarios": [s.to_dict() for s in self.scenarios],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Portfolio":
        data = migrate(data)  # upgrade older files to the current schema first
        metadata = data.get("metadata", {})
        return cls(
            household=Household.from_dict(data.get("household", {})),
            assumptions=Assumptions.from_dict(data["assumptions"]),
            entities=[Entity.from_dict(e) for e in data.get("entities", [])],
            assets=[Asset.from_dict(a) for a in data.get("assets", [])],
            cashflows=[Cashflow.from_dict(cf) for cf in data.get("cashflows", [])],
            liabilities=[liability_from_dict(li) for li in data.get("liabilities", [])],
            events=[Event.from_dict(ev) for ev in data.get("events", [])],
            bundles=[Bundle.from_dict(b) for b in data.get("bundles", [])],
            scenarios=[Scenario.from_dict(s) for s in data.get("scenarios", [])],
            file_version=metadata.get("file_version", CURRENT_SCHEMA_VERSION),
            created_at=metadata.get("created_at", ""),
            updated_at=metadata.get("updated_at", ""),
            metadata_notes=metadata.get("notes", ""),
        )
