"""
taxes.py

Placeholder household tax model for the MVP.

Design intent (per project spec): do not overbuild this. The job of this
module is to turn a year's taxable cashflows into a clean, auditable
TaxEstimate using flat rates by tax category, while keeping a structure
that a more accurate tax layer (brackets, deductions, AMT, state tax,
NIIT, etc.) can later replace without changing simulation.py's calling
convention -- simulation.py only calls estimate_taxes_for_year() and reads
a TaxEstimate back.
"""

from __future__ import annotations

from typing import List

from models import Assumptions, Cashflow, TaxEstimate, TaxCategory


def estimate_taxes_for_year(
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
