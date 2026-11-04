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
    SELF_EMPLOYMENT_INCOME = "self_employment_income"  # consulting etc.: ordinary + self-employment tax
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
    CROP_SALES = "crop_sales"
    CONSULTING_INCOME = "consulting_income"
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
    # Whose earnings or benefit this is (a Person): wages and self-employment
    # income are taxed per person (each has their own Social Security wage
    # base), and a Social Security benefit is subject to its owner's earnings
    # test. None = the first person in the household.
    person_id: Optional[str] = None
    # {year: annual amount} that replaces the planned amount in that year.
    # Filled in on a scenario's working copy from recorded Actuals; never saved.
    year_overrides: Dict[int, float] = field(default_factory=dict, repr=False, compare=False)

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

    def month_is_active(self, year: int, month: int) -> bool:
        if year < self.start_year or (self.end_year is not None and year > self.end_year):
            return False
        first = self.start_month if year == self.start_year else 1
        last = self.end_month if (self.end_year is not None and year == self.end_year) else 12
        return first <= month <= last

    def planned_amount_in_year(self, year: int, inflation_rate: float = 0.0) -> float:
        """The PLAN for a year, ignoring any recorded actuals."""
        overrides, self.year_overrides = self.year_overrides, {}
        try:
            return self.annual_amount_in_year(year, inflation_rate)
        finally:
            self.year_overrides = overrides

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

        If year_overrides has this year (from recorded actuals/forecasts), that
        amount is returned instead of the plan.
        """
        if year in self.year_overrides:
            return self.year_overrides[year]
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
        d.pop("year_overrides", None)  # derived at run time, never saved
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
class Person:
    """A member of the household. Ages drive events ("when Sam turns 65"),
    and the results table shows everyone's age by year.

    life_expectancy is a PLANNING age, not a prediction: plan to age 92+ so
    you don't run short if you live long. Use a "death" event tied to a
    person's age to model survivor scenarios -- end their pension at it,
    start a survivor benefit at it, and move it in a scenario.
    """

    person_id: str
    name: str
    birth_year: int
    birth_month: int = 1
    life_expectancy: int = 92
    notes: str = ""
    # Social Security earnings test, first year of benefits: a month counts as
    # "retired" (benefit paid in full) only if you DON'T perform substantial
    # services in your own business that month (SSA: generally over 45 hours).
    # True = assume you do, so self-employment months aren't treated as retired.
    substantial_self_employment: bool = True

    def __post_init__(self) -> None:
        _require(bool(self.person_id), "Person.person_id is required")
        _require(bool(self.name), "Person.name is required")
        _require(1 <= self.birth_month <= 12, f"Person '{self.name}': birth_month must be 1-12")
        _require(1900 <= self.birth_year <= 2100, f"Person '{self.name}': birth_year looks wrong")
        _require(0 < self.life_expectancy <= 120, f"Person '{self.name}': life_expectancy must be 1-120")

    def age_in(self, year: int) -> int:
        """Age reached during `year` (on the birthday)."""
        return year - self.birth_year

    def full_retirement_age(self) -> Tuple[int, int]:
        """Social Security full retirement age as (years, months), by birth year."""
        y = self.birth_year
        if y <= 1937:
            return (65, 0)
        if y <= 1942:
            return (65, 2 * (y - 1937))
        if y <= 1954:
            return (66, 0)
        if y <= 1959:
            return (66, 2 * (y - 1954))
        return (67, 0)

    def full_retirement_date(self) -> Tuple[int, int]:
        """(year, month) this person reaches full retirement age."""
        years, months = self.full_retirement_age()
        total = (self.birth_year + years) * 12 + (self.birth_month - 1) + months
        return total // 12, total % 12 + 1

    def date_at_age(self, age: int) -> Tuple[int, int]:
        """(year, month) of the birthday when this person turns `age`."""
        return self.birth_year + age, self.birth_month

    @property
    def final_year(self) -> int:
        return self.birth_year + self.life_expectancy

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Person":
        return cls(**data)


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

    AGE-BASED events: set person_id and age, and the date is that person's
    birthday at that age ("Sam turns 65" -> Sam's birth month, birth_year + 65).
    A scenario can change the age: {"event_sam_claims_ss": {"age": 70}}.
    """

    event_id: str
    name: str
    year: int
    month: int = 1
    notes: str = ""
    person_id: Optional[str] = None
    age: Optional[int] = None

    def __post_init__(self) -> None:
        _require(bool(self.event_id), "Event.event_id is required")
        _require(bool(self.name), "Event.name is required")
        _require(1 <= self.month <= 12, f"Event '{self.event_id}': month must be 1-12")
        _require(
            (self.person_id is None) == (self.age is None),
            f"Event '{self.name}': an age-based event needs both a person and an age",
        )

    @property
    def is_age_based(self) -> bool:
        return self.person_id is not None

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
    # Who belongs to an entity (and as what) lives in Portfolio.memberships.

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


class MembershipRole(str, Enum):
    OWNER = "owner"
    MEMBER = "member"                 # LLC member
    MANAGER = "manager"               # LLC manager
    PARTNER = "partner"
    GENERAL_PARTNER = "general_partner"
    LIMITED_PARTNER = "limited_partner"
    TRUSTEE = "trustee"
    BENEFICIARY = "beneficiary"
    OTHER = "other"


@dataclass
class Membership:
    """A LINK between a Person and an Entity, with the role the person plays.

    People and entities are many-to-many: Phil is in his personal entity AND
    the joint entity; the joint entity holds Phil AND Debbie. A link class
    (like a join table in SQL) is used instead of a list because the link
    carries its own information -- the role:

        entity_joint         person_phil    owner
        entity_joint         person_debbie  owner
        entity_family_trust  person_phil    trustee

    The same person can hold several roles in one entity (one Membership each).
    """

    entity_id: str
    person_id: str
    role: MembershipRole = MembershipRole.OWNER
    notes: str = ""

    def __post_init__(self) -> None:
        self.role = _enum_value(self.role, MembershipRole)
        _require(bool(self.entity_id) and bool(self.person_id), "Membership needs an entity and a person")

    @property
    def key(self) -> str:
        """Unique identity of a link: entity + person + role."""
        return f"{self.entity_id}|{self.person_id}|{self.role.value}"

    def to_dict(self) -> Dict[str, Any]:
        return {"entity_id": self.entity_id, "person_id": self.person_id,
                "role": self.role.value, "notes": self.notes}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Membership":
        return cls(**data)


@dataclass
class Ownership:
    """An entity owning a PERCENTAGE of another entity or of an asset.

        owner                    owned                       percent
        Philip & Debbie (joint)  Neufeld Family LP (entity)  1.00
        Neufeld Family LP        LVM (entity)                0.0645
        Neufeld Ventures         LVM (entity)                0.01
        Branfeld Investments     2049 Broadway (asset)       0.333

    Your share of anything = multiply the percents along each path from an
    entity nobody owns (your household), then add up the paths. LVM above:
    1.00 x 0.0645 + 1.00 x 0.01 = 7.45%.

    percent is a fraction (6.45% = 0.0645). Only the owners you list count:
    LVM's other investors don't need to be entered.
    """

    owner_entity_id: str
    owned_type: str          # "entity" or "asset"
    owned_id: str
    percent: float
    notes: str = ""

    def __post_init__(self) -> None:
        _require(self.owned_type in ("entity", "asset"), "Ownership.owned_type must be 'entity' or 'asset'")
        _require(bool(self.owner_entity_id) and bool(self.owned_id), "Ownership needs an owner and something owned")
        _require(0 < self.percent <= 1,
                 f"Ownership percent is a fraction between 0 and 1 (6.45% = 0.0645), got {self.percent}")
        _require(not (self.owned_type == "entity" and self.owned_id == self.owner_entity_id),
                 "An entity can't own itself")

    @property
    def key(self) -> str:
        return f"{self.owner_entity_id}|{self.owned_type}|{self.owned_id}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Ownership":
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
    # Month of the sale (the first month the asset is gone), set from end_event_id.
    # None = sold at the end of sale_year.
    sale_month: Optional[int] = None
    # Depreciation (detailed tax method): straight-line over recovery_years
    # from placed_in_service_year. Basis = the depreciable part (building,
    # trees, equipment -- never land). Typical recovery periods: 27.5
    # residential rental, 39 commercial; orchards and equipment vary.
    depreciable_basis: float = 0.0
    placed_in_service_year: Optional[int] = None
    recovery_years: float = 0.0
    # Business/farm income from this asset is subject to self-employment tax.
    subject_to_se_tax: bool = False
    # Whose self-employment income it is (a Person). Each spouse's payroll and
    # self-employment taxes are worked out separately. None = the first person.
    earner_person_id: Optional[str] = None
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

    def depreciation_in_year(self, year: int) -> float:
        """Straight-line depreciation for one calendar year: basis / recovery
        years, a partial year at the end (27.5 years -> half in the last year),
        nothing after the recovery period or after the asset is sold.
        (Simplified: no mid-month convention, no bonus depreciation.)"""
        if self.depreciable_basis <= 0 or self.recovery_years <= 0 or self.placed_in_service_year is None:
            return 0.0
        if year < self.placed_in_service_year or (self.sale_year is not None and year > self.sale_year):
            return 0.0
        elapsed = year - self.placed_in_service_year
        portion = min(1.0, max(0.0, self.recovery_years - elapsed))
        if year == self.sale_year and self.sale_month:
            portion = min(portion, (self.sale_month - 1) / 12)  # only the months it was held
        return self.depreciable_basis / self.recovery_years * portion

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
            "sale_month": self.sale_month,
            "depreciable_basis": self.depreciable_basis,
            "placed_in_service_year": self.placed_in_service_year,
            "recovery_years": self.recovery_years,
            "subject_to_se_tax": self.subject_to_se_tax,
            "earner_person_id": self.earner_person_id,
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


def months_between(from_year: int, from_month: int, to_year: int, to_month: int) -> int:
    """(2016, 1) -> (2026, 1) = 120."""
    return (to_year - from_year) * 12 + (to_month - from_month)


def balance_after_payments(principal: float, annual_rate: float, payment: float, payments: int) -> float:
    """What's still owed after `payments` monthly payments (closed-form amortization):
    B_k = P(1+r)^k - pmt * ((1+r)^k - 1) / r."""
    if payments <= 0:
        return principal
    r = annual_rate / 12.0
    if r == 0:
        return max(0.0, principal - payment * payments)
    growth = (1 + r) ** payments
    return max(0.0, principal * growth - payment * (growth - 1) / r)


def months_to_pay_off(balance: float, annual_rate: float, payment: float) -> Optional[int]:
    """How many monthly payments are left (rounded up), or None if the payment
    never pays it off. n = -ln(1 - B r / pmt) / ln(1 + r)."""
    import math
    if balance <= 0.005:
        return 0
    r = annual_rate / 12.0
    if r == 0:
        return math.ceil(balance / payment) if payment > 0 else None
    if payment <= balance * r:
        return None
    n = -math.log(1 - balance * r / payment) / math.log(1 + r)
    whole = math.floor(n)
    # a payment rounded to the cent can leave a few cents over: don't count an extra month for that
    if balance_after_payments(balance, annual_rate, payment, whole) < 1.0:
        return whole
    return whole + 1


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
    # Pay the remaining balance in full in this year (refinance, payoff),
    # at the start of payoff_month (None = at the end of the year).
    payoff_year: Optional[int] = None
    payoff_month: Optional[int] = None
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

    def year_activity(self, beginning_balance: float, months: int = 12) -> Tuple[float, float, float]:
        """Return (interest_paid, principal_paid, ending_balance) for the first
        `months` months of a year that starts at beginning_balance (12 = the
        whole year; fewer when the loan is paid off part-way through).
        Each subclass defines this."""
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
            "payoff_month": self.payoff_month,
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
    # Original terms, for reference (and to work out the balance -- see from_original_terms).
    original_amount: Optional[float] = None
    original_term_months: Optional[int] = None
    origination_year: Optional[int] = None    # month/year of the FIRST payment
    origination_month: int = 1

    kind: ClassVar[str] = "loan"

    @classmethod
    def from_original_terms(cls, *, liability_id: str, name: str, original_amount: float,
                            annual_interest_rate: float, original_term_months: int, origination_year: int,
                            origination_month: int = 1, as_of_year: int, monthly_payment: Optional[float] = None,
                            **kwargs) -> "Loan":
        """Build a loan from what's on the note -- amount, rate, term, first payment
        date -- with the balance and months left worked out as of January of
        `as_of_year` (the plan's start). monthly_payment overrides the calculated
        principal-and-interest payment if the lender bills a different amount.

        Example: $300,000 at 6% for 30 years, first payment Jan 2016 -> as of Jan 2026,
        120 payments made, 240 left, balance $251,057.17, payment $1,798.65."""
        _require(original_amount > 0, f"Loan '{name}': original amount must be > 0")
        _require(original_term_months > 0, f"Loan '{name}': original term must be > 0")
        payment = monthly_payment or amortized_monthly_payment(original_amount, annual_interest_rate,
                                                               original_term_months)
        made = max(0, months_between(origination_year, origination_month, as_of_year, 1))
        left = original_term_months - made
        _require(left > 0, f"Loan '{name}': by Jan {as_of_year} all {original_term_months} payments are made")
        balance = round(balance_after_payments(original_amount, annual_interest_rate, payment, made), 2)
        return cls(liability_id=liability_id, name=name, balance=balance, annual_interest_rate=annual_interest_rate,
                   start_year=as_of_year, term_months=left, monthly_payment=monthly_payment,
                   original_amount=original_amount, original_term_months=original_term_months,
                   origination_year=origination_year, origination_month=origination_month, **kwargs)

    def months_left(self) -> Optional[int]:
        """Payments left as of January of start_year."""
        if self.monthly_payment is None and self.term_months is not None:
            return self.term_months
        return months_to_pay_off(self.balance, self.annual_interest_rate, self.scheduled_monthly_payment())

    def payoff_date(self) -> Optional[Tuple[int, int]]:
        """(year, month) of the last scheduled payment, counting from Jan of start_year."""
        left = self.months_left()
        if left is None:
            return None
        if left == 0:
            return (self.start_year - 1, 12)
        return (self.start_year + (left - 1) // 12, (left - 1) % 12 + 1)

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

    def year_activity(self, beginning_balance: float, months: int = 12) -> Tuple[float, float, float]:
        """Walk `months` monthly payments. Each month: interest = balance x rate/12;
        the rest of the payment reduces the balance. Stops at payoff."""
        monthly_rate = self.annual_interest_rate / 12
        payment = self.scheduled_monthly_payment()
        balance = beginning_balance
        interest_paid = 0.0
        principal_paid = 0.0
        for _month in range(months):
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
        data.update({"term_months": self.term_months, "monthly_payment": self.monthly_payment,
                     "original_amount": self.original_amount, "original_term_months": self.original_term_months,
                     "origination_year": self.origination_year, "origination_month": self.origination_month})
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

    def year_activity(self, beginning_balance: float, months: int = 12) -> Tuple[float, float, float]:
        return beginning_balance * self.annual_interest_rate * months / 12, 0.0, beginning_balance

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
# Actual (what really happened, or a near-term forecast)
# ---------------------------------------------------------------------------

ACTUAL_STATUSES = ("confirmed", "provisional", "estimated")


@dataclass
class Actual:
    """A recorded amount for one year (or one month of a year).

    kind: "actual"   -- what really came in or went out (e.g. 2026)
          "forecast" -- your best near-term estimate (e.g. 2027)

    amount is what YOUR household received or paid (your share), always
    positive; direction says which way it went.

    flow_id links it to the planned Cashflow it measures. Linked records
    replace the plan in simulations for that year (actual beats forecast
    beats plan; a part-year of monthly records is topped up with the plan
    for the other months). Unlinked records are "not in the plan" and only
    show in the plan-vs-actual report.
    """

    actual_id: str
    year: int
    amount: float
    name: str = ""
    kind: str = "actual"
    direction: CashflowDirection = CashflowDirection.INFLOW
    category: FlowType = FlowType.OTHER
    month: Optional[int] = None
    flow_id: Optional[str] = None
    asset_id: Optional[str] = None
    entity_id: Optional[str] = None
    notes: str = ""
    # How sure the number is: "confirmed" (verified against a statement),
    # "provisional" (recorded but not yet reconciled) or "estimated".
    status: str = "estimated"
    # A QuickBooks-like class, e.g. "Crop 2025" -- groups records that belong
    # together even when the cash lands in different calendar years.
    record_class: str = ""

    def __post_init__(self) -> None:
        self.direction = _enum_value(self.direction, CashflowDirection)
        self.category = _enum_value(self.category, FlowType)
        self.status = (self.status or "estimated").lower()
        _require(self.status in ACTUAL_STATUSES,
                 f"Actual '{self.name}': status must be one of {', '.join(ACTUAL_STATUSES)}")
        _require(bool(self.actual_id), "Actual.actual_id is required")
        _require(self.kind in ("actual", "forecast"), f"Actual '{self.name}': kind must be 'actual' or 'forecast'")
        _require(self.amount >= 0, f"Actual '{self.name}': amount must be positive (direction says in or out)")
        _require(self.month is None or 1 <= self.month <= 12, f"Actual '{self.name}': month must be 1-12")

    @property
    def slot(self) -> tuple:
        """Two records in the same slot describe the same thing (re-imports replace).
        A linked record is identified by its cashflow and name (so several receipts for one
        flow -- e.g. crop sales and crop insurance -- can sit side by side); an unlinked one
        by name and asset."""
        if self.flow_id:
            return ("flow", self.flow_id, self.name.lower(), self.year, self.month, self.kind)
        return ("name", self.name.lower(), self.asset_id, self.year, self.month, self.kind)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["direction"] = self.direction.value
        d["category"] = self.category.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Actual":
        return cls(**data)


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
    # "detailed": federal + state brackets, deductions, depreciation (taxes.py)
    # "flat":     one flat rate per income category (default_tax_rates)
    tax_method: str = "detailed"
    filing_status: str = "married_joint"   # or "single"
    state: str = "CA"                      # "CA" or "none"

    def __post_init__(self) -> None:
        _require(self.end_year >= self.start_year, "Assumptions.end_year must be >= start_year")
        _require(self.tax_method in ("detailed", "flat"), "Assumptions.tax_method must be 'detailed' or 'flat'")
        _require(self.filing_status in ("married_joint", "single"),
                 "Assumptions.filing_status must be 'married_joint' or 'single'")
        _require(self.state in ("CA", "none"), "Assumptions.state must be 'CA' or 'none'")
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

    # "share": scale everything by your ownership % (the default).
    # "full":  the 100% view -- every asset, loan and cashflow at full size.
    ownership_view: str = "share"

    # Use recorded actuals/forecasts in place of the plan where they exist.
    use_actuals: bool = True

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
    details: Dict[str, float] = field(default_factory=dict)  # line items (detailed method)

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
    ages: Dict[str, int] = field(default_factory=dict)  # person name -> age reached this year
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
            "ages": dict(self.ages),
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
    # The scenario's working copy after the run (e.g. Social Security after the
    # earnings test), for drill-down views. Not saved.
    working_portfolio: Any = field(default=None, repr=False, compare=False)

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
    people: List[Person] = field(default_factory=list)
    memberships: List[Membership] = field(default_factory=list)
    ownerships: List[Ownership] = field(default_factory=list)
    actuals: List[Actual] = field(default_factory=list)
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

        person_ids = [p.person_id for p in self.people]
        dup_people = {x for x in person_ids if person_ids.count(x) > 1}
        _require(not dup_people, f"Duplicate person_id(s) found: {dup_people}")
        entity_id_list = [e.entity_id for e in self.entities]
        seen_links = set()
        for m in self.memberships:
            _require(m.entity_id in entity_id_list, f"Membership points to unknown entity '{m.entity_id}'")
            _require(m.person_id in person_ids, f"Membership points to unknown person '{m.person_id}'")
            _require(m.key not in seen_links, f"Duplicate membership: {m.key}")
            seen_links.add(m.key)
        for e in self.entities:
            self._check_one_personal_owner(e, self.memberships)
        self._check_ownerships()
        actual_ids = [a.actual_id for a in self.actuals]
        _require(len(actual_ids) == len(set(actual_ids)), "Duplicate actual_id(s) found")
        known_flows = {cf.flow_id for cf in self.cashflows}
        for a in self.actuals:
            _require(a.flow_id is None or a.flow_id in known_flows,
                     f"Actual '{a.name}' points to cashflow '{a.flow_id}' which doesn't exist")
        for ev in self.events:
            _require(ev.person_id is None or ev.person_id in person_ids,
                     f"Event '{ev.name}' is tied to person '{ev.person_id}' who doesn't exist")

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
        people = {p.person_id: p for p in self.people}
        for ev in self.events:  # age-based events get their date from the birthday
            if ev.person_id in people:
                ev.year, ev.month = people[ev.person_id].date_at_age(ev.age)
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
                a.sale_year, a.sale_month = events[a.end_event_id].year, events[a.end_event_id].month
        for li in self.liabilities:
            if li.start_event_id in events:
                li.start_year = events[li.start_event_id].year
            if li.end_event_id in events:
                li.payoff_year, li.payoff_month = events[li.end_event_id].year, events[li.end_event_id].month

    def add_event(self, event: Event) -> None:
        _require(self.get_event(event.event_id) is None, f"Event with id '{event.event_id}' already exists")
        _require(event.person_id is None or self.get_person(event.person_id) is not None,
                 f"Event '{event.name}' is tied to unknown person '{event.person_id}'")
        self.events.append(event)
        self.apply_event_dates()

    # -- people ------------------------------------------------------------------

    def get_person(self, person_id: str) -> Optional[Person]:
        for p in self.people:
            if p.person_id == person_id:
                return p
        return None

    def add_person(self, person: Person) -> None:
        _require(self.get_person(person.person_id) is None, f"Person with id '{person.person_id}' already exists")
        self.people.append(person)

    def replace_person(self, person_id: str, new_person: Person) -> None:
        for i, p in enumerate(self.people):
            if p.person_id == person_id:
                self.people[i] = new_person
                self.apply_event_dates()  # a new birthday moves their age-based events
                return
        _require(False, f"No person with id '{person_id}' found to replace")

    def remove_person(self, person_id: str) -> None:
        """Remove a person. Their age-based events keep their current date
        but become fixed-date events."""
        _require(self.get_person(person_id) is not None, f"No person with id '{person_id}' found to remove")
        for ev in self.events:
            if ev.person_id == person_id:
                ev.person_id, ev.age = None, None
        self.memberships = [m for m in self.memberships if m.person_id != person_id]
        self.people = [p for p in self.people if p.person_id != person_id]

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
        for a in self.actuals:
            if a.flow_id == flow_id:
                a.flow_id = None  # the record stays, as "not in the plan"

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
        removed_ids = {cf.flow_id for cf in removed}
        for a in self.actuals:
            if a.flow_id in removed_ids:
                a.flow_id = None
            if a.asset_id == asset_id:
                a.asset_id = None
        self.ownerships = [o for o in self.ownerships if not (o.owned_type == "asset" and o.owned_id == asset_id)]
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

    def remove_entity(self, entity_id: str) -> None:
        """Remove an entity and its memberships (the people themselves stay)."""
        _require(self.get_entity(entity_id) is not None, f"No entity with id '{entity_id}' found to remove")
        self.entities = [e for e in self.entities if e.entity_id != entity_id]
        self.memberships = [m for m in self.memberships if m.entity_id != entity_id]
        self.ownerships = [o for o in self.ownerships
                           if o.owner_entity_id != entity_id and not (o.owned_type == "entity" and o.owned_id == entity_id)]

    # -- ownership % ----------------------------------------------------------------

    def _owners_of(self, owned_type: str, owned_id: str) -> List[Ownership]:
        return [o for o in self.ownerships if o.owned_type == owned_type and o.owned_id == owned_id]

    def _check_ownerships(self, ownerships: Optional[List[Ownership]] = None) -> None:
        """Every link points at real things, nothing is owned more than 100%,
        and there are no loops (A owns B owns A)."""
        ownerships = self.ownerships if ownerships is None else ownerships
        entity_ids = {e.entity_id for e in self.entities}
        asset_ids = {a.asset_id for a in self.assets}
        label = {e.entity_id: e.name for e in self.entities}
        label.update({a.asset_id: a.name for a in self.assets})
        keys, totals, owners_of = set(), {}, {}
        for o in ownerships:
            _require(o.owner_entity_id in entity_ids, f"Ownership owner '{o.owner_entity_id}' isn't an entity")
            _require(o.owned_id in (entity_ids if o.owned_type == "entity" else asset_ids),
                     f"Ownership points at unknown {o.owned_type} '{o.owned_id}'")
            _require(o.key not in keys, f"Duplicate ownership link: {o.key}")
            keys.add(o.key)
            target = (o.owned_type, o.owned_id)
            totals[target] = totals.get(target, 0.0) + o.percent
            _require(totals[target] <= 1.0 + 1e-9,
                     f"'{label.get(o.owned_id, o.owned_id)}' would be owned more than 100% in total "
                     f"({totals[target]:.2%})")
            if o.owned_type == "entity":
                owners_of.setdefault(o.owned_id, []).append(o.owner_entity_id)

        def visit(entity_id, path):
            _require(entity_id not in path,
                     "Ownership loop: " + " -> ".join(label.get(x, x) for x in list(path) + [entity_id])
                     + " (an entity would end up owning itself)")
            for owner in owners_of.get(entity_id, []):
                visit(owner, path + [entity_id])

        for entity_id in owners_of:
            visit(entity_id, [])

    def entity_share(self, entity_id: str) -> float:
        """Your effective share of an entity: 1.0 if nobody owns it (it's yours),
        otherwise the sum over its owners of (owner's share x percent).
        Recursive: each owner's share is worked out the same way."""
        owners = self._owners_of("entity", entity_id)
        if not owners:
            return 1.0
        return sum(self.entity_share(o.owner_entity_id) * o.percent for o in owners)

    def asset_share(self, asset_id: str) -> float:
        """Your share of an asset: through explicit ownership links if it has
        any, otherwise through the entity that holds it (100% of that entity's
        share), otherwise 100%."""
        owners = self._owners_of("asset", asset_id)
        if owners:
            return sum(self.entity_share(o.owner_entity_id) * o.percent for o in owners)
        asset = self.get_asset(asset_id)
        if asset is not None and asset.entity_id:
            return self.entity_share(asset.entity_id)
        return 1.0

    def liability_share(self, liability: "Liability") -> float:
        """A loan follows the asset that secures it, else the entity that owes it."""
        if liability.secured_by_asset_id:
            return self.asset_share(liability.secured_by_asset_id)
        if liability.entity_id:
            return self.entity_share(liability.entity_id)
        return 1.0

    def share_paths(self, owned_type: str, owned_id: str) -> List[Tuple[List[str], float]]:
        """Explain a share: every ownership path (as entity names) and its share.
        e.g. [(['Philip & Debbie', 'Family LP', 'LVM'], 0.0645), ...]"""
        names = {e.entity_id: e.name for e in self.entities}
        if owned_type == "asset" and not self._owners_of("asset", owned_id):
            asset = self.get_asset(owned_id)
            if asset is None or not asset.entity_id:
                return [([asset.name if asset else owned_id], 1.0)]
            return [(path + [asset.name], share) for path, share in self.share_paths("entity", asset.entity_id)]
        label = names.get(owned_id, owned_id) if owned_type == "entity" else self.get_asset(owned_id).name
        owners = self._owners_of(owned_type, owned_id)
        if not owners:
            return [([label], 1.0)]
        paths = []
        for o in owners:
            for path, share in self.share_paths("entity", o.owner_entity_id):
                paths.append((path + [label], share * o.percent))
        return paths

    def apply_ownership_shares(self) -> None:
        """Scale every asset, its cashflows and its loans by your share, IN
        PLACE. Only ever called on a scenario's working copy, never on the
        stored portfolio. Household-level cashflows (salary, living
        expenses) aren't owned by an entity and stay at 100%."""
        asset_shares = {a.asset_id: self.asset_share(a.asset_id) for a in self.assets}
        liability_shares = {li.liability_id: self.liability_share(li) for li in self.liabilities}
        for a in self.assets:
            s = asset_shares[a.asset_id]
            a.initial_value *= s
            a.current_value *= s
            a.depreciable_basis *= s
        for cf in self.cashflows:
            if cf.asset_id is not None:
                s = asset_shares.get(cf.asset_id, 1.0)
                cf.amount *= s
                cf.year_overrides = {y: v * s for y, v in cf.year_overrides.items()}
        for li in self.liabilities:
            s = liability_shares[li.liability_id]
            li.balance *= s
            for attr in ("monthly_payment", "credit_limit"):
                if getattr(li, attr, None) is not None:
                    setattr(li, attr, getattr(li, attr) * s)

    def actual_annual_amount(self, cf: Cashflow, year: int, inflation_rate: float = 0.0):
        """What the linked records say for one cashflow in one year, in YOUR
        share, as (amount, source) -- or None if nothing is recorded.

        Priority: full-year actual > full-year forecast > monthly records
        (actual beats forecast month by month) topped up with the plan for
        months with no record. source is "actual", "forecast",
        or "actual + plan" / "forecast + plan" for a part year.
        """
        records = [a for a in self.actuals if a.flow_id == cf.flow_id and a.year == year]
        if not records:
            return None
        for kind in ("actual", "forecast"):
            full = [a.amount for a in records if a.kind == kind and a.month is None]
            if full:
                return sum(full), kind
        by_month = {}
        for a in sorted(records, key=lambda r: r.kind == "actual"):  # actuals overwrite forecasts
            by_month[a.month] = a
        share = self.asset_share(cf.asset_id) if cf.asset_id else 1.0
        active = [m for m in range(1, 13) if cf.month_is_active(year, m)]
        planned_year = cf.planned_amount_in_year(year, inflation_rate) * share
        planned_per_month = planned_year / len(active) if active else 0.0
        recorded = sum(a.amount for a in by_month.values())
        rest = sum(planned_per_month for m in active if m not in by_month)
        kinds = {a.kind for a in by_month.values()}
        label = "actual" if kinds == {"actual"} else "forecast" if kinds == {"forecast"} else "actual/forecast"
        return recorded + rest, (label if rest == 0 else f"{label} + plan")

    def apply_actuals(self, inflation_rate: float = 0.0) -> None:
        """On a scenario's working copy: put recorded amounts in place of the
        plan (cf.year_overrides). Stored in 100%-of-asset terms (divided by
        your share) so that apply_ownership_shares() brings them back to
        exactly what you recorded."""
        years_by_flow = {}
        for a in self.actuals:
            if a.flow_id:
                years_by_flow.setdefault(a.flow_id, set()).add(a.year)
        for cf in self.cashflows:
            share = self.asset_share(cf.asset_id) if cf.asset_id else 1.0
            for year in years_by_flow.get(cf.flow_id, ()):
                result = self.actual_annual_amount(cf, year, inflation_rate)
                if result is not None and share > 0:
                    cf.year_overrides[year] = result[0] / share

    def add_actual(self, actual: Actual) -> None:
        """Add a record; one already in the same slot (same item, year, month
        and kind) is replaced, so re-importing a corrected file is safe."""
        _require(actual.flow_id is None or self.get_cashflow(actual.flow_id) is not None,
                 f"Actual '{actual.name}' points to unknown cashflow '{actual.flow_id}'")
        self.actuals = [a for a in self.actuals if a.slot != actual.slot]
        self.actuals.append(actual)

    def remove_actual(self, actual_id: str) -> None:
        _require(any(a.actual_id == actual_id for a in self.actuals), f"No actual '{actual_id}' found to remove")
        self.actuals = [a for a in self.actuals if a.actual_id != actual_id]

    def add_ownership(self, ownership: Ownership) -> None:
        self._check_ownerships(self.ownerships + [ownership])  # checks loops and totals first
        self.ownerships.append(ownership)

    def remove_ownership(self, key: str) -> None:
        _require(any(o.key == key for o in self.ownerships), f"No ownership link '{key}' found to remove")
        self.ownerships = [o for o in self.ownerships if o.key != key]

    # -- memberships (people <-> entities, many-to-many) -----------------------------

    def people_in(self, entity_id: str) -> List[Tuple[Person, MembershipRole]]:
        """Everyone linked to an entity, with their role."""
        return [(self.get_person(m.person_id), m.role) for m in self.memberships if m.entity_id == entity_id]

    def entities_of(self, person_id: str) -> List[Tuple[Entity, MembershipRole]]:
        """Every entity a person is linked to, with their role -- the reverse lookup."""
        return [(self.get_entity(m.entity_id), m.role) for m in self.memberships if m.person_id == person_id]

    def _check_one_personal_owner(self, entity: Entity, memberships: List[Membership]) -> None:
        """A personal entity IS one person: it can have at most one owner.
        (Use a joint entity for two owners.)"""
        if entity.entity_type != EntityType.PERSONAL:
            return
        owners = {m.person_id for m in memberships
                  if m.entity_id == entity.entity_id and m.role == MembershipRole.OWNER}
        _require(len(owners) <= 1,
                 f"'{entity.name}' is a personal entity, so it can have only one owner "
                 f"(found {len(owners)}). Use a joint entity for shared ownership.")

    def add_membership(self, membership: Membership) -> None:
        _require(self.get_entity(membership.entity_id) is not None, f"Unknown entity '{membership.entity_id}'")
        _require(self.get_person(membership.person_id) is not None, f"Unknown person '{membership.person_id}'")
        _require(all(m.key != membership.key for m in self.memberships),
                 "That person already has that role in that entity")
        self._check_one_personal_owner(self.get_entity(membership.entity_id), self.memberships + [membership])
        self.memberships.append(membership)

    def remove_membership(self, key: str) -> None:
        _require(any(m.key == key for m in self.memberships), f"No membership '{key}' found to remove")
        self.memberships = [m for m in self.memberships if m.key != key]

    def replace_entity(self, entity_id: str, new_entity: Entity) -> None:
        self._check_one_personal_owner(new_entity, self.memberships)  # e.g. switching joint -> personal
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
            "people": [p.to_dict() for p in self.people],
            "memberships": [m.to_dict() for m in self.memberships],
            "ownerships": [o.to_dict() for o in self.ownerships],
            "actuals": [a.to_dict() for a in self.actuals],
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
            people=[Person.from_dict(p) for p in data.get("people", [])],
            memberships=[Membership.from_dict(m) for m in data.get("memberships", [])],
            ownerships=[Ownership.from_dict(o) for o in data.get("ownerships", [])],
            actuals=[Actual.from_dict(a) for a in data.get("actuals", [])],
            bundles=[Bundle.from_dict(b) for b in data.get("bundles", [])],
            scenarios=[Scenario.from_dict(s) for s in data.get("scenarios", [])],
            file_version=metadata.get("file_version", CURRENT_SCHEMA_VERSION),
            created_at=metadata.get("created_at", ""),
            updated_at=metadata.get("updated_at", ""),
            metadata_notes=metadata.get("notes", ""),
        )
