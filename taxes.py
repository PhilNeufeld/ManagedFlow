"""
taxes.py

Household income tax for one simulated year. Two methods (Assumptions.tax_method):

- "flat":     one flat rate per income category (Assumptions.default_tax_rates).
              Simple and transparent; the original MVP method.
- "detailed": federal and California tax the way the returns work, simplified:
      1. Income by type: wages, other ordinary income (pensions, interest,
         unitrusts...), self-employment, capital gains and qualified
         dividends, Social Security.
      2. Rentals and farms/businesses: NET income = taxable receipts
         - deductible expenses - loan interest - DEPRECIATION, per asset.
         Rental losses follow the passive-loss rules: up to $25,000 can
         offset other income, phased out between $100,000 and $150,000 of
         income; the rest is carried forward and used against later rental
         income (released in full when a rental is sold).
      3. Social Security: the taxable part from "provisional income".
      4. Deduction: the larger of the standard deduction (+ extra for 65+)
         and itemized (state income + property tax up to the SALT cap,
         plus mortgage interest on non-rental property); plus the 2025-2028
         senior deduction.
      5. Federal tax: ordinary brackets, then capital gains/qualified
         dividends stacked on top at 0/15/20%; Net Investment Income Tax;
         FICA on wages; self-employment tax; additional Medicare tax.
      6. California: its own brackets and standard deduction, no tax on
         Social Security, capital gains taxed as ordinary income, exemption
         credits, 1% extra over $1M.

  Not modeled (be aware): AMT, tax credits other than CA exemption credits,
  depreciation recapture and capital gain on a property sale (enter the
  gain as a capital-gains cashflow), qualified business income deduction,
  student-loan and charitable deductions, CA exemption-credit phase-out,
  IRMAA. Brackets after the table year are indexed by your inflation
  assumption. All numbers live in tax_tables.py with their sources.

simulation.py only calls estimate_taxes_for_year() and reads a TaxEstimate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import tax_tables as T
from models import (
    Assumptions, Cashflow, CashflowDirection, FlowType, LiabilityType, TaxCategory, TaxEstimate,
)

# Outflows on a rental or business asset that are deductible against its income.
# (Mortgage PAYMENTS aren't: only the interest part is, which comes from the
# Liability. Capital improvements are depreciated, not expensed.)
DEDUCTIBLE_EXPENSE_TYPES = {
    FlowType.PROPERTY_TAX, FlowType.INSURANCE, FlowType.REPAIRS, FlowType.OPERATING_EXPENSE,
    FlowType.MANAGEMENT_FEE, FlowType.LOAN_INTEREST,
}


@dataclass
class TaxContext:
    """Everything the detailed method needs beyond the year's cashflows."""
    assets: list                          # assets in this run (working copy, your share)
    flows_by_asset: Dict[str, list]
    household_flows: list                 # cashflows with no asset
    liability_results: list               # this year's AnnualLiabilityResults
    liabilities: Dict[str, object]        # liability_id -> Liability
    people: list = field(default_factory=list)
    sold_asset_ids: set = field(default_factory=set)
    carryforward: Dict[str, float] = field(default_factory=dict)  # state across years
    # Interest that accrues inside accounts (Asset.interest_taxed_yearly): asset_id -> this year's growth.
    accrued_interest: Dict[str, float] = field(default_factory=dict)


def estimate_taxes_for_year(
    year: int,
    cashflows_this_year: List[Cashflow],
    assumptions: Assumptions,
    context: Optional[TaxContext] = None,
) -> TaxEstimate:
    if getattr(assumptions, "tax_method", "flat") == "detailed" and context is not None:
        return estimate_detailed(year, assumptions, context)
    return estimate_flat(year, cashflows_this_year, assumptions)


def _default_person(ctx: TaxContext) -> str:
    return ctx.people[0].person_id if ctx.people else "household"


def earned_income_by_person(year: int, inflation: float, ctx: TaxContext) -> Dict[str, Dict[str, float]]:
    """Each person's {"wages": ..., "se": net self-employment income} for the year.

    Payroll tax, self-employment tax and the Social Security earnings test are
    all PER PERSON (each spouse has their own wage base). A flow's person_id
    (or a business asset's earner_person_id) says whose it is; unassigned
    earnings go to the first person. Self-employment from a business/farm asset
    is its net: taxable receipts - deductible expenses - loan interest - depreciation.
    """
    known = {p.person_id for p in ctx.people}
    default = _default_person(ctx)
    out: Dict[str, Dict[str, float]] = {}

    def add(pid, key, value):
        pid = pid if pid in known else default
        out.setdefault(pid, {"wages": 0.0, "se": 0.0})[key] += value

    def earned(cf):
        value = cf.annual_amount_in_year(year, inflation)
        if value <= 0 or cf.direction != CashflowDirection.INFLOW or not cf.taxable:
            return
        if cf.flow_type == FlowType.SALARY_INCOME:
            add(cf.person_id, "wages", value)
        elif cf.tax_category == TaxCategory.SELF_EMPLOYMENT_INCOME:
            add(cf.person_id, "se", value)

    for cf in ctx.household_flows:
        earned(cf)
    interest_by_asset: Dict[Optional[str], float] = {}
    for lr in ctx.liability_results:
        li = ctx.liabilities.get(lr.liability_id)
        if li is not None and lr.interest_paid > 0:
            interest_by_asset[li.secured_by_asset_id] = interest_by_asset.get(li.secured_by_asset_id, 0.0) + lr.interest_paid
    for asset in ctx.assets:
        flows = ctx.flows_by_asset.get(asset.asset_id, [])
        if asset.tax_category not in (TaxCategory.RENTAL_INCOME, TaxCategory.BUSINESS_INCOME):
            for cf in flows:
                earned(cf)
            continue
        if asset.tax_category != TaxCategory.BUSINESS_INCOME or not asset.subject_to_se_tax:
            continue
        net = -interest_by_asset.get(asset.asset_id, 0.0) - asset.depreciation_in_year(year)
        for cf in flows:
            value = cf.annual_amount_in_year(year, inflation)
            if value <= 0:
                continue
            if (cf.direction == CashflowDirection.INFLOW and cf.taxable
                    and cf.flow_type != FlowType.SOCIAL_SECURITY_INCOME
                    and cf.tax_category not in (TaxCategory.CAPITAL_GAINS, TaxCategory.QUALIFIED_DIVIDENDS)):
                net += value
            elif cf.direction == CashflowDirection.OUTFLOW and cf.flow_type in DEDUCTIBLE_EXPENSE_TYPES:
                net -= value
        add(asset.earner_person_id, "se", net)
    return out


def estimate_detailed(year: int, assumptions: Assumptions, ctx: TaxContext) -> TaxEstimate:
    fs = assumptions.filing_status
    infl = assumptions.inflation_rate
    fed_idx = lambda amount: T.index_amount(amount, T.FEDERAL_YEAR, year, infl)  # noqa: E731
    ca_idx = lambda amount: T.index_amount(amount, T.CALIFORNIA_YEAR, year, infl)  # noqa: E731
    max_people = 2 if fs == "married_joint" else 1
    seniors = min(max_people, sum(1 for p in ctx.people if p.age_in(year) >= 65))

    def amount(cf) -> float:
        return cf.annual_amount_in_year(year, infl)

    wages = ordinary = interest = qualified_div = cap_gains = social_security = 0.0
    se_household = business_net = 0.0
    se_business = 0.0
    personal_mortgage_interest = personal_property_tax = 0.0
    rental_nets: Dict[str, float] = {}
    depreciation_total = 0.0

    def classify_income(cf, value):
        nonlocal wages, ordinary, interest, qualified_div, cap_gains, se_household, business_net
        if cf.flow_type == FlowType.SALARY_INCOME:
            wages += value
        elif cf.tax_category == TaxCategory.SELF_EMPLOYMENT_INCOME:
            se_household += value
        elif cf.tax_category == TaxCategory.CAPITAL_GAINS:
            cap_gains += value
        elif cf.tax_category == TaxCategory.QUALIFIED_DIVIDENDS:
            qualified_div += value
        elif cf.flow_type == FlowType.INTEREST_INCOME:
            interest += value
        elif cf.tax_category == TaxCategory.BUSINESS_INCOME:
            business_net += value
        else:  # pensions, annuities, trusts, other ordinary income
            ordinary += value

    # --- household-level cashflows -------------------------------------------------
    for cf in ctx.household_flows:
        value = amount(cf)
        if value <= 0:
            continue
        if cf.direction == CashflowDirection.INFLOW:
            if cf.flow_type == FlowType.SOCIAL_SECURITY_INCOME:
                social_security += value
            elif cf.taxable:
                classify_income(cf, value)
        elif cf.direction == CashflowDirection.OUTFLOW and cf.flow_type == FlowType.PROPERTY_TAX:
            personal_property_tax += value

    # Interest earned inside accounts (bank, CD): taxed as it accrues, though it stays in the account.
    interest += sum(v for v in (ctx.accrued_interest or {}).values() if v > 0)

    # --- interest paid on each asset's loans ----------------------------------------
    interest_by_asset: Dict[Optional[str], float] = {}
    for lr in ctx.liability_results:
        li = ctx.liabilities.get(lr.liability_id)
        if li is None or lr.interest_paid <= 0:
            continue
        interest_by_asset[li.secured_by_asset_id] = interest_by_asset.get(li.secured_by_asset_id, 0.0) + lr.interest_paid

    # --- assets: rentals and businesses net out; others pass income through ----------
    for asset in ctx.assets:
        if asset.tax_category == TaxCategory.RENTAL_INCOME:
            kind = "rental"
        elif asset.tax_category == TaxCategory.BUSINESS_INCOME:
            kind = "business"
        else:
            kind = "other"
        receipts = expenses = 0.0
        for cf in ctx.flows_by_asset.get(asset.asset_id, []):
            value = amount(cf)
            if value <= 0:
                continue
            if cf.direction == CashflowDirection.INFLOW:
                if cf.flow_type == FlowType.SOCIAL_SECURITY_INCOME:
                    social_security += value
                elif not cf.taxable:
                    continue
                elif kind != "other" and cf.tax_category not in (TaxCategory.CAPITAL_GAINS,
                                                                  TaxCategory.QUALIFIED_DIVIDENDS):
                    receipts += value
                else:
                    classify_income(cf, value)
            elif cf.direction == CashflowDirection.OUTFLOW:
                if kind != "other" and cf.flow_type in DEDUCTIBLE_EXPENSE_TYPES:
                    expenses += value
                elif kind == "other" and cf.flow_type == FlowType.PROPERTY_TAX:
                    personal_property_tax += value
        if kind == "other":
            personal_mortgage_interest += interest_by_asset.get(asset.asset_id, 0.0)
            continue
        dep = asset.depreciation_in_year(year)
        depreciation_total += dep
        net = receipts - expenses - interest_by_asset.get(asset.asset_id, 0.0) - dep
        if kind == "rental":
            rental_nets[asset.asset_id] = net
        else:
            business_net += net
            if asset.subject_to_se_tax and net > 0:
                se_business += net

    # --- passive rental losses ----------------------------------------------------------
    rental_before = sum(rental_nets.values())
    suspended = ctx.carryforward.get("passive_loss", 0.0)
    rental = rental_before
    released = 0.0
    if any(aid in ctx.sold_asset_ids for aid in rental_nets):
        released, rental, suspended = suspended, rental - suspended, 0.0  # sale releases suspended losses
    elif rental > 0 and suspended > 0:
        used = min(suspended, rental)
        rental, suspended = rental - used, suspended - used
    if rental < 0 and not released:
        income_for_allowance = wages + ordinary + interest + business_net + se_household + cap_gains + qualified_div
        allowance = max(0.0, T.PASSIVE_ALLOWANCE
                        - T.PASSIVE_PHASEOUT_RATE * max(0.0, income_for_allowance - T.PASSIVE_PHASEOUT_START))
        allowed = max(rental, -allowance)
        suspended += allowed - rental
        rental = allowed
    ctx.carryforward["passive_loss"] = suspended

    # --- payroll and self-employment taxes, PER PERSON ------------------------------------------
    # Each person has their own Social Security wage base; their wages use it
    # first, and their self-employment earnings fill what's left.
    wage_base = T.ss_wage_base(year, infl)
    names = {p.person_id: p.name for p in ctx.people}
    fica = se_tax = se_earnings = 0.0
    payroll_by_person: Dict[str, float] = {}
    for pid, e in earned_income_by_person(year, infl, ctx).items():
        w = e["wages"]
        nese = max(0.0, e["se"]) * T.SE_EARNINGS_FACTOR
        fica_p = min(w, wage_base) * T.SOCIAL_SECURITY_RATE + w * T.MEDICARE_RATE
        se_p = (min(nese, max(0.0, wage_base - w)) * T.SOCIAL_SECURITY_RATE * 2
                + nese * T.MEDICARE_RATE * 2)
        fica, se_tax, se_earnings = fica + fica_p, se_tax + se_p, se_earnings + nese
        who = names.get(pid, "Household")
        payroll_by_person[f"FICA - {who}"] = fica_p
        payroll_by_person[f"Self-employment tax - {who}"] = se_p
    half_se = se_tax / 2
    additional_medicare = T.ADDITIONAL_MEDICARE_RATE * max(   # joint threshold on combined earnings
        0.0, wages + se_earnings - T.ADDITIONAL_MEDICARE_THRESHOLD[fs])

    # --- AGI, with the taxable part of Social Security ------------------------------------
    net_capital = max(cap_gains, -3_000.0)  # capital losses: $3,000/yr against other income
    other_income = (wages + ordinary + interest + se_household + business_net + rental
                    + net_capital + qualified_div - half_se)
    base1, base2, cap_amount = T.SS_BASE[fs]
    provisional = other_income + 0.5 * social_security
    if provisional <= base1:
        taxable_ss = 0.0
    elif provisional <= base2:
        taxable_ss = min(0.5 * social_security, 0.5 * (provisional - base1))
    else:
        taxable_ss = min(0.85 * social_security,
                         0.85 * (provisional - base2) + min(0.5 * social_security, cap_amount))
    agi = other_income + taxable_ss
    magi = agi

    # --- California (computed first: it's part of the federal SALT deduction) ------------------
    ca_tax = 0.0
    ca_taxable = 0.0
    if assumptions.state == "CA":
        ca_agi = agi - taxable_ss
        ca_itemized = personal_mortgage_interest + personal_property_tax
        ca_deduction = max(ca_idx(T.CA_STANDARD_DEDUCTION[fs]), ca_itemized)
        ca_taxable = max(0.0, ca_agi - ca_deduction)
        ca_before_credits = T.tax_from_brackets(
            ca_taxable, T.indexed_brackets(T.CA_BRACKETS[fs], T.CALIFORNIA_YEAR, year, infl))
        ca_before_credits += T.CA_MENTAL_HEALTH_RATE * max(0.0, ca_taxable - T.CA_MENTAL_HEALTH_THRESHOLD)
        credits = ca_idx(T.CA_PERSONAL_CREDIT) * max_people + ca_idx(T.CA_SENIOR_CREDIT) * seniors
        ca_tax = max(0.0, ca_before_credits - credits)

    # --- federal deduction ---------------------------------------------------------------------------
    standard = fed_idx(T.STANDARD_DEDUCTION[fs]) + seniors * fed_idx(T.ADDITIONAL_65_PER_PERSON[fs])
    salt = min(T.salt_cap(year, magi, infl), ca_tax + personal_property_tax)
    itemized = salt + personal_mortgage_interest
    deduction = max(standard, itemized)
    senior_deduction = 0.0
    if year in T.SENIOR_DEDUCTION_YEARS:
        per_person = max(0.0, T.SENIOR_DEDUCTION
                         - T.SENIOR_PHASEOUT_RATE * max(0.0, magi - T.SENIOR_PHASEOUT_START[fs]))
        senior_deduction = per_person * seniors
    taxable = max(0.0, agi - deduction - senior_deduction)

    # --- federal tax: ordinary brackets, then capital gains stacked on top -------------------------
    preferential = min(max(0.0, net_capital) + qualified_div, taxable)
    ordinary_taxable = taxable - preferential
    fed_ordinary = T.tax_from_brackets(
        ordinary_taxable, T.indexed_brackets(T.FEDERAL_BRACKETS[fs], T.FEDERAL_YEAR, year, infl))
    fed_capital, lower = 0.0, ordinary_taxable
    for top, rate in T.indexed_brackets(T.CAPITAL_GAINS_BANDS[fs], T.FEDERAL_YEAR, year, infl):
        upper = taxable if top is None else min(taxable, top)
        if upper > lower:
            fed_capital += (upper - lower) * rate
            lower = upper
    investment_income = interest + qualified_div + max(0.0, net_capital) + max(0.0, rental)
    niit = T.NIIT_RATE * max(0.0, min(investment_income, magi - T.NIIT_THRESHOLD[fs]))

    federal_income_tax = fed_ordinary + fed_capital
    taxes = {
        "federal_income": federal_income_tax, "california": ca_tax, "fica": fica,
        "self_employment": se_tax, "niit": niit, "additional_medicare": additional_medicare,
    }
    total = sum(taxes.values())
    income_by_type = {
        "wages": wages, "other_ordinary": ordinary + interest, "self_employment": se_household + se_business,
        "business_net": business_net, "rental_net": rental, "capital_gains_and_qualified_dividends":
            max(0.0, net_capital) + qualified_div, "social_security": social_security,
    }
    details = {
        "Wages": wages, "Other ordinary income": ordinary + interest, "Self-employment income": se_household,
        "Business/farm net": business_net, "Rental net (before passive limits)": rental_before,
        "Rental net (allowed this year)": rental, "Depreciation": depreciation_total,
        "Suspended rental losses carried forward": suspended, "Capital gains + qualified dividends":
            max(0.0, net_capital) + qualified_div, "Social Security received": social_security,
        "Social Security taxable": taxable_ss, "AGI": agi, "Standard deduction": standard,
        "Itemized deductions": itemized, "Deduction used": deduction, "Senior deduction": senior_deduction,
        "Taxable income": taxable, "Federal tax on ordinary income": fed_ordinary,
        "Federal tax on capital gains": fed_capital, "NIIT": niit, "FICA": fica,
        "Self-employment tax": se_tax, **payroll_by_person, "Social Security wage base (per person)": wage_base,
        "Additional Medicare tax": additional_medicare,
        "CA taxable income": ca_taxable, "CA tax": ca_tax, "Total tax": total,
        "Effective rate on AGI": (total / agi) if agi > 0 else 0.0,
    }
    return TaxEstimate(year=year, taxable_income_by_category=income_by_type, tax_by_category=taxes,
                       total_estimated_tax=total, details=details)


def estimate_flat(
    year: int,
    cashflows_this_year: List[Cashflow],
    assumptions: Assumptions,
) -> TaxEstimate:
    """Estimate household tax for a single year using flat rates by tax
    category, applied to each taxable cashflow's signed annual amount.

    Only inflow-direction, taxable=True cashflows contribute taxable income.
    Outflow cashflows (e.g. a "tax_payment" flow itself, or expenses) are
    never treated as taxable income here, even if marked taxable=True by
    mistake, since that would double count.
    """
    taxable_income_by_category: dict[str, float] = {}

    for cf in cashflows_this_year:
        if not cf.taxable:
            continue
        if cf.direction.value != "inflow":
            continue

        amount = cf.annual_amount_in_year(year)
        if amount <= 0:
            continue

        key = cf.tax_category.value
        taxable_income_by_category[key] = taxable_income_by_category.get(key, 0.0) + amount

    tax_by_category: dict[str, float] = {}
    total_tax = 0.0
    for category_key, income in taxable_income_by_category.items():
        rate = assumptions.default_tax_rates.get(category_key, 0.0)
        tax = income * rate
        tax_by_category[category_key] = tax
        total_tax += tax

    return TaxEstimate(
        year=year,
        taxable_income_by_category=taxable_income_by_category,
        tax_by_category=tax_by_category,
        total_estimated_tax=total_tax,
    )
