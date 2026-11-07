"""Cash flow summary: each year's income, expenses, debt service and tax grouped into
plain categories (salaries, pensions, rents, living expenses, mortgages...), with
years as columns. Built from the simulation's own line items and loan results, so
every category adds back to the year's totals; anything that can't be placed shows
as an "Other" row rather than disappearing."""
from __future__ import annotations

from typing import Dict, List, Optional

TOLERANCE = 0.5

# flow_type -> income category
INCOME_CATEGORIES = [
    ("Salaries & consulting", {"salary_income", "consulting_income"}),
    ("Pensions & annuities", {"pension_income", "annuity_income", "pars_income"}),
    ("Social Security", {"social_security_income"}),
    ("Rents", {"rent_income"}),
    ("Business & farm income", {"crop_sales", "management_fee"}),
    ("Distributions (businesses & trusts)", {"business_distribution", "trust_distribution"}),
    ("Interest & dividends", {"interest_income", "dividend_income"}),
    ("Principal repaid to you (not taxable)", {"loan_principal_repayment"}),
    ("Account withdrawals & RMDs", {"withdrawal"}),
    ("Sales & refinancing", {"sale_proceeds", "refinance_proceeds"}),
]
OTHER_INCOME = "Other income"

EXPENSE_CATEGORIES = ["Living expenses", "Rental property costs", "Business & farm costs",
                      "Other household expenses"]

DEBT_CATEGORIES = ["Rental property loans", "Business & farm loans", "Home mortgage",
                   "HELOC & credit lines", "Car loans", "Student loans", "Other loans"]

PRINCIPAL_RETURNED = "Principal repaid to you (not taxable)"


def _working(result, portfolio):
    return getattr(result, "working_portfolio", None) or portfolio


def _rental_assets(result) -> set:
    """Assets that earn rent in any year -- their costs and loans count as rental."""
    return {li["asset_id"] for r in result.annual_results for li in r.line_items
            if li.get("flow_type") == "rent_income" and li.get("asset_id") is not None}


def _income_category(flow_type: str) -> str:
    for name, types in INCOME_CATEGORIES:
        if flow_type in types:
            return name
    return OTHER_INCOME


def _expense_category(line: dict, rentals: set) -> str:
    if line.get("flow_type") == "living_expense":
        return "Living expenses"
    aid = line.get("asset_id")
    if aid is None:
        return "Other household expenses"
    return "Rental property costs" if aid in rentals else "Business & farm costs"


def _debt_category(liability, rentals: set, asset_types: Dict) -> str:
    kind = getattr(getattr(liability, "liability_type", None), "value", str(getattr(liability, "liability_type", "")))
    secured = getattr(liability, "secured_by_asset_id", None)
    if kind in ("heloc", "line_of_credit"):
        return "HELOC & credit lines"
    if kind == "car":
        return "Car loans"
    if kind == "student":
        return "Student loans"
    if secured is not None and secured in rentals:
        return "Rental property loans"
    if kind in ("farm", "business") or asset_types.get(secured) == "business":
        return "Business & farm loans"
    if kind == "mortgage":
        return "Home mortgage"
    return "Other loans"


def build(result, portfolio, years: Optional[List[int]] = None) -> List[dict]:
    """The summary as rows: {"Section", "Line", "Kind", <year>: amount, ...}.

    Kind is "item" (a category), "detail" (one line inside a category: its "Line" is
    the item's name and "Category" its category), "total", "net" or "memo". Income
    and expenses are positive; the Net row is income - expenses - tax."""
    working = _working(result, portfolio)
    by_year = {r.year: r for r in result.annual_results}
    years = [y for y in (years or sorted(by_year)) if y in by_year]
    rentals = _rental_assets(result)
    asset_types = {a.asset_id: getattr(a.asset_type, "value", str(a.asset_type)) for a in working.assets}
    loans = {li.liability_id: li for li in working.liabilities}

    cats: Dict[tuple, Dict[int, float]] = {}
    details: Dict[tuple, Dict[int, float]] = {}

    def add(section, category, name, year, amount):
        cats.setdefault((section, category), {})[year] = cats.get((section, category), {}).get(year, 0.0) + amount
        key = (section, category, name)
        details.setdefault(key, {})[year] = details.get(key, {}).get(year, 0.0) + amount

    for y in years:
        r = by_year[y]
        inc = exp = debt = 0.0
        for li in r.line_items:
            amt = li.get("amount", 0.0) or 0.0
            if li.get("section") == "income":
                add("Income", _income_category(li.get("flow_type", "")), li.get("name", ""), y, amt)
                inc += amt
            elif li.get("section") == "expense":
                add("Expenses", _expense_category(li, rentals), li.get("name", ""), y, amt)
                exp += amt
        for lr in r.liability_results:
            paid = (lr.interest_paid or 0.0) + (lr.principal_paid or 0.0)
            if not paid:
                continue
            loan = loans.get(lr.liability_id)
            cat = _debt_category(loan, rentals, asset_types) if loan else "Other loans"
            add("Debt service", cat, loan.name if loan else str(lr.liability_id), y, paid)
            debt += paid
        # Anything the totals have that the items don't: shown, never dropped
        gaps = (("Income", OTHER_INCOME, r.total_gross_income - inc),
                ("Expenses", "Other household expenses", r.total_expenses - r.total_debt_service - exp),
                ("Debt service", "Other loans", r.total_debt_service - debt))
        for section, cat, gap in gaps:
            if abs(gap) > TOLERANCE:
                add(section, cat, "Not itemized", y, gap)

    rows: List[dict] = []

    def row(section, line, kind, values, category=""):
        d = {"Section": section, "Line": line, "Kind": kind, "Category": category}
        d.update({y: values.get(y, 0.0) for y in years})
        rows.append(d)

    def section_rows(section, order, total_label, total_fn):
        present = [c for c in order if any(abs(v) > TOLERANCE for v in cats.get((section, c), {}).values())]
        for c in present:
            row(section, c, "item", cats[(section, c)])
            for (s, cat, name), vals in details.items():
                if s == section and cat == c and any(abs(v) > TOLERANCE for v in vals.values()):
                    row(section, name, "detail", vals, category=c)
        row(section, total_label, "total", {y: total_fn(by_year[y]) for y in years})

    section_rows("Income", [n for n, _ in INCOME_CATEGORIES] + [OTHER_INCOME], "Total income",
                 lambda r: r.total_gross_income)
    section_rows("Expenses", EXPENSE_CATEGORIES, "Total operating expenses",
                 lambda r: r.total_expenses - r.total_debt_service)
    section_rows("Debt service", DEBT_CATEGORIES, "Total debt service (interest + principal)",
                 lambda r: r.total_debt_service)
    row("Tax", "Est. income tax", "total", {y: by_year[y].total_estimated_tax for y in years})
    row("Net", "Net cash flow (income - expenses - debt service - tax)", "net",
        {y: by_year[y].surplus_or_deficit for y in years})
    returned = cats.get(("Income", PRINCIPAL_RETURNED), {})
    if any(abs(v) > TOLERANCE for v in returned.values()):
        row("Memo", "Net cash flow excluding principal repaid to you", "memo",
            {y: by_year[y].surplus_or_deficit - returned.get(y, 0.0) for y in years})
    row("Memo", "Withdrawn from savings", "memo", {y: by_year[y].withdrawal_amount for y in years})
    row("Memo", "Reinvested", "memo", {y: by_year[y].reinvestment_amount for y in years})
    row("Memo", "Liquid assets, end of year", "memo", {y: by_year[y].liquid_net_worth for y in years})
    row("Memo", "Total net worth, end of year", "memo", {y: by_year[y].total_net_worth for y in years})
    return rows


def check(rows: List[dict], result, years: List[int]) -> List[dict]:
    """Each section's categories vs. its total, per year (should all be OK)."""
    by_year = {r.year: r for r in result.annual_results}
    out = []
    for section in ("Income", "Expenses", "Debt service"):
        items = [r for r in rows if r["Section"] == section and r["Kind"] == "item"]
        total = next(r for r in rows if r["Section"] == section and r["Kind"] == "total")
        for y in years:
            s = sum(r[y] for r in items)
            out.append({"Year": y, "Section": section, "Total": total[y], "Sum of categories": s,
                        "OK": abs(s - total[y]) <= TOLERANCE})
    for y in years:
        r = by_year[y]
        net = r.total_gross_income - r.total_expenses - r.total_estimated_tax
        out.append({"Year": y, "Section": "Net", "Total": r.surplus_or_deficit, "Sum of categories": net,
                    "OK": abs(net - r.surplus_or_deficit) <= TOLERANCE})
    return out
