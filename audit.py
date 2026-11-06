"""
audit.py

Every number in the simulation's annual summary, broken into the items that make it
up, with checks that the items add back to the totals. Used by the Audit tab and
the Audit sheets of the Excel export.

audit_rows(result, portfolio) -> one flat list of rows (long format), each:
    Scenario, Year, Section, Line, Detail, Source ID, Asset, Entity, Amount

Sections, in order:
    Gross income      each inflow: household cashflows and each asset's flows
    Expenses          each outflow, plus each loan's payments (debt service is an expense)
    Debt service      each loan: beginning balance, interest, principal, ending balance
    Est. tax          the tax calculation step by step (federal, payroll, California)
    Cash              surplus/(deficit), and where it went: withdrawals and reinvestment by asset
    Assets            each asset: beginning value, growth, cashflows that change its value,
                      other changes (RMD, receivable paydown, sale), withdrawn, reinvested, ending value

checks(result) -> per year: each summary total vs. the sum of its items.
pivot(rows, ...) -> the rows for some years as a table with years as columns.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

SECTIONS = ["Gross income", "Expenses", "Debt service", "Est. tax", "Cash", "Assets"]
# Flow sections add up across years; balance sections (Debt service, Assets) don't.
FLOW_SECTIONS = {"Gross income", "Expenses", "Est. tax", "Cash"}
COLUMNS = ["Scenario", "Year", "Section", "Line", "Detail", "Source ID", "Asset", "Entity", "Amount"]
TOLERANCE = 0.01


def _names(portfolio, result=None):
    """asset_id -> (asset name, entity name); liability_id -> (name, entity name)."""
    working = getattr(result, "working_portfolio", None) or portfolio
    entity = {e.entity_id: e.name for e in getattr(working, "entities", [])}
    assets = {a.asset_id: a for a in working.assets}
    asset_names = {aid: (a.name, entity.get(a.entity_id, "")) for aid, a in assets.items()}
    loans = {li.liability_id: (li.name, entity.get(getattr(li, "entity_id", None), "")
                               or asset_names.get(getattr(li, "secured_by_asset_id", None), ("", ""))[1])
             for li in working.liabilities}
    active = {aid for aid, a in assets.items() if a.active}
    return asset_names, loans, active


def audit_rows(result, portfolio, scenario_name: Optional[str] = None,
               years: Optional[Iterable[int]] = None) -> List[dict]:
    """All audit rows for one scenario's SimulationResult (optionally only some years)."""
    asset_names, loans, active = _names(portfolio, result)
    scen = scenario_name or str(result.scenario_id)
    wanted = set(years) if years is not None else None
    rows: List[dict] = []

    def add(year, section, line, amount, detail="", source="", asset_id=None, entity=None):
        a_name, a_entity = asset_names.get(asset_id, ("", ""))
        rows.append({"Scenario": scen, "Year": year, "Section": section, "Line": line, "Detail": detail,
                     "Source ID": "" if source is None else str(source), "Asset": a_name,
                     "Entity": entity if entity is not None else (a_entity or ("Household" if asset_id is None else "")),
                     "Amount": round(float(amount), 2)})

    for y in result.annual_results:
        if wanted is not None and y.year not in wanted:
            continue
        yr = y.year
        for li in y.line_items:
            if li["section"] == "income":
                add(yr, "Gross income", li["name"], li["amount"], li.get("flow_type", ""), li["flow_id"], li.get("asset_id"))
        for li in y.line_items:
            if li["section"] == "expense":
                add(yr, "Expenses", li["name"], li["amount"], li.get("flow_type", ""), li["flow_id"], li.get("asset_id"))
        for lr in y.liability_results:
            name, ent = loans.get(lr.liability_id, (str(lr.liability_id), ""))
            if lr.debt_service:
                add(yr, "Expenses", f"{name} - payments", lr.debt_service, "debt service", f"loan:{lr.liability_id}",
                    entity=ent)
        for lr in y.liability_results:
            name, ent = loans.get(lr.liability_id, (str(lr.liability_id), ""))
            if not (lr.beginning_balance or lr.ending_balance or lr.debt_service):
                continue
            for detail, amount in (("Beginning balance", lr.beginning_balance), ("Interest", lr.interest_paid),
                                   ("Principal", lr.principal_paid), ("Ending balance", lr.ending_balance)):
                add(yr, "Debt service", name, amount, detail, f"loan:{lr.liability_id}", entity=ent)
        te = y.tax_estimate
        if te is not None:
            if te.details:
                for key, value in te.details.items():
                    if isinstance(value, (int, float)):
                        add(yr, "Est. tax", key, value, "rate" if "rate" in key.lower() else "", entity="Household")
            else:
                for cat, value in te.tax_by_category.items():
                    add(yr, "Est. tax", f"Tax on {cat.replace('_', ' ')}", value, "flat rate", entity="Household")
            add(yr, "Est. tax", "Total estimated tax", te.total_estimated_tax, "total", entity="Household")
        add(yr, "Cash", "Gross income", y.total_gross_income, "+", entity="Household")
        add(yr, "Cash", "Expenses (incl. debt service)", -y.total_expenses, "-", entity="Household")
        add(yr, "Cash", "Est. tax", -y.total_estimated_tax, "-", entity="Household")
        add(yr, "Cash", "Surplus/(deficit)", y.surplus_or_deficit, "=", entity="Household")
        for aid, amt in y.withdrawal_breakdown.items():
            add(yr, "Cash", "Withdrawn to cover the shortfall", amt, "withdrawal", aid, aid)
        for aid, amt in y.reinvestment_breakdown.items():
            add(yr, "Cash", "Surplus reinvested", amt, "reinvestment", aid, aid)
        if y.surplus_or_deficit > 0 and not y.reinvestment_breakdown:
            add(yr, "Cash", "Surplus not reinvested (no liquid asset, or reinvesting is off)",
                y.surplus_or_deficit, "unallocated", entity="Household")
        for ar in y.asset_results:
            if ar.asset_id not in active:
                continue
            withdrawn = y.withdrawal_breakdown.get(ar.asset_id, 0.0)
            reinvested = y.reinvestment_breakdown.get(ar.asset_id, 0.0)
            other = ar.ending_value - (ar.beginning_value + ar.growth_amount + ar.net_cashflow_affecting_value)
            end = ar.ending_value - withdrawn + reinvested
            if not any((ar.beginning_value, end)):
                continue
            src = f"asset:{ar.asset_id}"
            for detail, amount in (("Beginning value", ar.beginning_value), ("Growth", ar.growth_amount),
                                   ("Cashflows that change its value", ar.net_cashflow_affecting_value),
                                   ("Other changes (RMD, receivable paydown, sale)", other),
                                   ("Withdrawn", -withdrawn), ("Reinvested", reinvested), ("Ending value", end)):
                if amount or detail in ("Beginning value", "Ending value"):
                    add(yr, "Assets", asset_names.get(ar.asset_id, (str(ar.asset_id),))[0], amount, detail, src,
                        ar.asset_id)
    return rows


def checks(result, years: Optional[Iterable[int]] = None) -> List[dict]:
    """For each year: each summary total vs. the sum of its items. OK = within one cent."""
    wanted = set(years) if years is not None else None
    out = []
    for y in result.annual_results:
        if wanted is not None and y.year not in wanted:
            continue
        income = sum(li["amount"] for li in y.line_items if li["section"] == "income")
        expense = sum(li["amount"] for li in y.line_items if li["section"] == "expense")
        debt = sum(lr.debt_service for lr in y.liability_results)
        te = y.tax_estimate
        tax_lines = te.details.get("Total tax", te.total_estimated_tax) if (te and te.details) else (
            sum(te.tax_by_category.values()) if te else 0.0)
        pairs = [
            ("Gross income", y.total_gross_income, income),
            ("Expenses", y.total_expenses, expense + debt),
            ("Debt service", y.total_debt_service, debt),
            ("Est. tax", y.total_estimated_tax, tax_lines),
            ("Surplus/(deficit)", y.surplus_or_deficit, y.total_gross_income - y.total_expenses - y.total_estimated_tax),
            ("Withdrawal", y.withdrawal_amount, sum(y.withdrawal_breakdown.values())),
            ("Reinvestment", y.reinvestment_amount, sum(y.reinvestment_breakdown.values())),
            ("Total liabilities", y.total_liabilities, sum(lr.ending_balance for lr in y.liability_results)),
            ("Total net worth", y.total_net_worth, y.total_assets - y.total_liabilities),
            ("Liquid + illiquid assets", y.total_assets, y.liquid_net_worth + y.illiquid_net_worth),
        ]
        for name, summary, items in pairs:
            out.append({"Year": y.year, "Check": name, "Summary": round(summary, 2), "Sum of items": round(items, 2),
                        "Difference": round(summary - items, 2), "OK": abs(summary - items) <= TOLERANCE})
    return out


def asset_total_check(result, portfolio, years: Optional[Iterable[int]] = None) -> List[dict]:
    """Total assets vs. the sum of each asset's ending value (from the roll-forward)."""
    rows = audit_rows(result, portfolio, years=years)
    out = []
    for y in result.annual_results:
        if years is not None and y.year not in set(years):
            continue
        total = sum(r["Amount"] for r in rows if r["Year"] == y.year and r["Section"] == "Assets"
                    and r["Detail"] == "Ending value")
        out.append({"Year": y.year, "Check": "Total assets", "Summary": round(y.total_assets, 2),
                    "Sum of items": round(total, 2), "Difference": round(y.total_assets - total, 2),
                    "OK": abs(y.total_assets - total) <= 1.0})   # each asset's value rounded to the cent
    return out


def pivot(rows: List[dict], section: str, years: List[int], entity: Optional[str] = None):
    """One section as a table: a row per line (and detail), a column per year, plus a Total
    column for flow sections. Returns a pandas DataFrame."""
    import pandas as pd
    picked = [r for r in rows if r["Section"] == section and r["Year"] in years
              and (entity is None or r["Entity"] == entity)]
    if not picked:
        return pd.DataFrame()
    df = pd.DataFrame(picked)
    keys = ["Line", "Detail", "Asset", "Entity", "Source ID"]
    order = list(dict.fromkeys(tuple(r[k] for k in keys) for r in picked))   # first-seen order
    table = df.pivot_table(index=keys, columns="Year", values="Amount", aggfunc="sum", fill_value=0.0, sort=False)
    table = table.reindex(order)
    table.columns = [str(c) for c in table.columns]
    if section in FLOW_SECTIONS and len(years) > 1 and section != "Est. tax":
        table["Total"] = table.sum(axis=1)
    return table.reset_index()
