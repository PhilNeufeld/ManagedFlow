"""
actuals.py

Bring in what really happened (actuals) and near-term forecasts, and compare
them with the plan.

1. parse_csv()   reads the CSV layout:
       year,kind,cashflow_id,name,category,direction,amount,month,asset_id,status,class,notes
2. link_rows()   links each row to its planned Cashflow BY KEY: the row's
                 cashflow_id is that cashflow's primary key. Names are never
                 used to link. A row without a cashflow_id is "not in the plan"
                 (it still counts once, in its own year). template_for() writes a
                 CSV listing every planned cashflow with its key, ready to fill in.
3. commit_rows() saves them as Actual records (re-importing replaces)
4. plan_vs_actual() and trend_breaks() build the reports

Amounts are what YOUR household received or paid (your share).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from models import ACTUAL_STATUSES, Actual, CashflowDirection, _key, FlowType, Portfolio, ValidationError

CSV_COLUMNS = ["year", "kind", "cashflow_id", "name", "category", "direction", "amount", "month", "asset_id",
               "status", "class", "notes"]
# Required: year, kind, name, amount. cashflow_id links a row to its planned cashflow (its key);
# category and direction may then be left blank (they come from the cashflow). Without a
# cashflow_id a row is "not in the plan": give its category and direction, and asset_id if
# it belongs to an asset. Extra columns (e.g. "asset", "entity" labels) are ignored.

# Friendly category names used in the CSV -> FlowType
CATEGORY_ALIASES: Dict[str, str] = {
    "salary": "salary_income", "consulting": "consulting_income", "pension": "pension_income",
    "social_security": "social_security_income", "rent": "rent_income", "crop_sales": "crop_sales",
    "distribution": "business_distribution", "unitrust": "trust_distribution", "interest": "interest_income",
    "dividends": "dividend_income", "loan_payment": "mortgage_payment", "property_tax": "property_tax",
    "insurance": "insurance", "repairs": "repairs", "operating_expense": "operating_expense",
    "management_fee": "management_fee", "living_expense": "living_expense", "tax_payment": "tax_payment",
    "other": "other",
}

TEMPLATE_CSV = (
    ",".join(CSV_COLUMNS) + "\n"
    "2026,actual,12,Crop sales,,,118500,,,confirmed,Crop 2026,Lower yield than planned\n"
    "2027,forecast,12,Crop sales,,,132000,,,estimated,Crop 2027,\n"
    "2026,actual,7,Rent,,,1150,3,,provisional,,March rent\n"
    "2026,actual,,Well pump repair,repairs,outflow,4200,8,3,confirmed,,Not in the plan: give category, direction and asset_id\n"
)


def template_for(portfolio: Portfolio, year: int, kind: str = "actual") -> str:
    """A CSV with one row per planned cashflow, keyed by cashflow_id, for `year`:
    fill in the amounts (blank rows are skipped on import). The 'asset' column is
    a label for reading only. Add rows without a cashflow_id for items not in the plan."""
    names = {a.asset_id: a.name for a in portfolio.assets}
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(CSV_COLUMNS + ["asset"])
    for cf in portfolio.cashflows:
        w.writerow([year, kind, cf.flow_id, cf.name, cf.flow_type.value, cf.direction.value, "", "",
                    cf.asset_id if cf.asset_id is not None else "", "", "", "",
                    names.get(cf.asset_id, "household")])
    return out.getvalue()


@dataclass
class ImportRow:
    line: int
    year: Optional[int] = None
    kind: str = "actual"
    name: str = ""
    category: str = "other"
    direction: str = "inflow"
    amount: Optional[float] = None
    month: Optional[int] = None
    entity_text: str = ""
    asset_text: str = ""
    notes: str = ""
    status: str = "estimated"
    record_class: str = ""
    cashflow_id: object = None      # key of the planned cashflow (from the CSV)
    # filled in by link_rows()
    asset_id: Optional[str] = None
    entity_id: Optional[str] = None
    flow_id: Optional[str] = None
    match_note: str = ""
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _money(text: str) -> float:
    cleaned = re.sub(r"[$,\s]", "", text or "")
    if cleaned.startswith("(") and cleaned.endswith(")"):  # accounting-style negative
        cleaned = "-" + cleaned[1:-1]
    return float(cleaned)


def parse_csv(text: str) -> List[ImportRow]:
    """Read CSV text into ImportRows, checking each value. Bad rows carry
    errors instead of stopping the import."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))  # tolerate Excel's BOM
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in ("year", "kind", "name", "amount") if c not in headers]
    if missing:
        raise ValueError(f"CSV is missing column(s): {', '.join(missing)}. Expected: {','.join(CSV_COLUMNS)}")

    rows = []
    for i, raw in enumerate(reader, start=2):  # line 1 is the header
        # Extra trailing commas put spare values under a None key -- ignore them.
        r = {k.strip().lower(): (v if isinstance(v, str) else "").strip() for k, v in raw.items() if k}
        if not any(r.values()):
            continue
        if not r.get("amount"):
            continue            # a template row nobody filled in
        row = ImportRow(line=i, name=r.get("name", ""), notes=r.get("notes", ""),
                        entity_text=r.get("entity", ""), asset_text=r.get("asset", ""))
        try:
            row.year = int(r.get("year", ""))
        except ValueError:
            row.errors.append(f"year '{r.get('year')}' isn't a year")
        row.kind = r.get("kind", "").lower()
        if row.kind not in ("actual", "forecast"):
            row.errors.append(f"kind must be actual or forecast, not '{row.kind}'")
        for col in ("cashflow_id", "asset_id"):
            if r.get(col):
                setattr(row, col, _key(r[col]))
        category = r.get("category", "").lower()
        row.category = CATEGORY_ALIASES.get(category, category)
        if row.category and row.category not in {t.value for t in FlowType}:
            row.errors.append(f"unknown category '{category}'")
        row.direction = r.get("direction", "").lower()
        if row.direction and row.direction not in ("inflow", "outflow"):
            row.errors.append(f"direction must be inflow or outflow, not '{row.direction}'")
        try:
            row.amount = _money(r.get("amount", ""))
            if row.amount < 0:
                row.errors.append("amount must be positive (direction says in or out)")
        except ValueError:
            row.errors.append(f"amount '{r.get('amount')}' isn't a number")
        if r.get("month"):
            try:
                row.month = int(r["month"])
                if not 1 <= row.month <= 12:
                    raise ValueError
            except ValueError:
                row.errors.append(f"month '{r['month']}' must be 1-12 or blank")
        if r.get("status"):
            row.status = r["status"].lower()
            if row.status not in ACTUAL_STATUSES:
                row.errors.append(f"status must be {', '.join(ACTUAL_STATUSES)} or blank, not '{r['status']}'")
        row.record_class = r.get("class", "")
        if not row.name:
            row.errors.append("name is blank")
        rows.append(row)
    return rows


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def link_rows(rows: List[ImportRow], portfolio: Portfolio) -> List[ImportRow]:
    """Link each row to its planned cashflow by KEY (the row's cashflow_id). Never by name.

    - cashflow_id given: it must be a planned cashflow's key; the row takes that
      cashflow's asset, and its category and direction if they were left blank
      (a direction that disagrees with the cashflow is an error).
    - no cashflow_id: the row is "not in the plan". It needs a category and a
      direction; asset_id, if given, must be an asset's key.
    """
    for row in rows:
        row.flow_id = None
        if row.cashflow_id is not None:
            cf = portfolio.get_cashflow(row.cashflow_id)
            if cf is None:
                row.errors.append(f"no planned cashflow #{row.cashflow_id}")
                continue
            row.flow_id, row.asset_id = cf.flow_id, cf.asset_id
            row.category = row.category or cf.flow_type.value
            row.direction = row.direction or cf.direction.value
            if row.direction != cf.direction.value:
                row.errors.append(f"direction '{row.direction}' disagrees with #{cf.flow_id} ({cf.direction.value})")
            row.match_note = f"linked to #{cf.flow_id} {cf.name}"
            continue
        if row.asset_id is not None and portfolio.get_asset(row.asset_id) is None:
            row.errors.append(f"no asset #{row.asset_id}")
        if not row.category:
            row.errors.append("category needed for an item not in the plan")
        if not row.direction:
            row.errors.append("direction needed for an item not in the plan")
        where = f"asset #{row.asset_id}" if row.asset_id is not None else "the household"
        row.match_note = f"not in the plan: counts once in {row.year}, on {where}"
    return rows


match_rows = link_rows   # old name


def commit_rows(rows: List[ImportRow], portfolio: Portfolio) -> int:
    """Save valid rows as Actual records. Returns how many were saved."""
    saved = 0
    for row in rows:
        if not row.ok:
            continue
        actual_id = portfolio.next_id("actual")
        try:
            portfolio.add_actual(Actual(
                actual_id=actual_id, year=row.year, amount=row.amount, name=row.name, kind=row.kind,
                direction=row.direction, category=row.category, month=row.month, flow_id=row.flow_id,
                asset_id=row.asset_id, entity_id=row.entity_id, notes=row.notes,
                status=row.status, record_class=row.record_class,
            ))
            saved += 1
        except ValidationError as exc:
            row.errors.append(str(exc))
    return saved


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def _share(portfolio: Portfolio, cf) -> float:
    return portfolio.asset_share(cf.asset_id) if cf.asset_id else 1.0


def _flag(variance: float, base: float, pct: float, dollars: float) -> bool:
    return abs(variance) >= dollars and (base == 0 or abs(variance) / abs(base) >= pct)


def plan_vs_actual(portfolio: Portfolio, year: int, pct: float = 0.10, dollars: float = 1_000) -> List[dict]:
    """One row per planned cashflow with a record in `year`, plus one per record
    that isn't in the plan. Amounts are your share. 'Flag' when the difference
    is at least `pct` of plan AND at least `dollars`."""
    inflation = portfolio.assumptions.inflation_rate
    rows = []
    linked = {a.flow_id for a in portfolio.actuals if a.year == year and a.flow_id}
    for cf in portfolio.cashflows:
        if cf.flow_id not in linked:
            continue
        planned = cf.planned_amount_in_year(year, inflation) * _share(portfolio, cf)
        recorded, source = portfolio.actual_annual_amount(cf, year, inflation)
        variance = recorded - planned
        rows.append({
            "Item": cf.name, "Direction": cf.direction.value, "Planned": planned, "Actual / forecast": recorded,
            "Source": source, "Difference": variance,
            "Difference %": (variance / planned) if planned else None,
            "Flag": _flag(variance, planned, pct, dollars), "flow_id": cf.flow_id,
        })
    for a in portfolio.actuals:
        if a.year == year and not a.flow_id:
            rows.append({
                "Item": a.name + (f" (month {a.month})" if a.month else ""), "Direction": a.direction.value,
                "Planned": 0.0, "Actual / forecast": a.amount, "Source": f"{a.kind}, NOT IN PLAN",
                "Difference": a.amount, "Difference %": None, "Flag": True, "flow_id": None,
            })
    return rows


def trend_breaks(portfolio: Portfolio, year: int, pct: float = 0.10, dollars: float = 1_000) -> List[dict]:
    """For each planned cashflow with a record in `year`: does that number
    continue the line's own path from the year before?

    Expected = last year's value (actual if recorded, else plan) grown by the
    line's growth rate (plus inflation if it's inflation-adjusted). A big gap
    means next year's number breaks from the longer-run shape of the curve.
    """
    inflation = portfolio.assumptions.inflation_rate
    rows = []
    linked = {a.flow_id for a in portfolio.actuals if a.year == year and a.flow_id}
    for cf in portfolio.cashflows:
        if cf.flow_id not in linked:
            continue
        share = _share(portfolio, cf)
        this_year, source = portfolio.actual_annual_amount(cf, year, inflation)
        prior = portfolio.actual_annual_amount(cf, year - 1, inflation)
        prior_value, prior_source = (prior if prior else
                                     (cf.planned_amount_in_year(year - 1, inflation) * share, "plan"))
        growth = (1 + cf.annual_growth_rate) * ((1 + inflation) if cf.inflation_adjusted else 1)
        expected = prior_value * growth
        gap = this_year - expected
        rows.append({
            "Item": cf.name, "Prior year": prior_value, "Prior year is": prior_source,
            "Growth used": growth - 1, "Expected": expected, "This year": this_year,
            "This year is": source, "Gap": gap, "Gap %": (gap / expected) if expected else None,
            "Plan for year": cf.planned_amount_in_year(year, inflation) * share,
            "Flag": _flag(gap, expected, pct, dollars), "flow_id": cf.flow_id,
        })
    return rows


def years_with_records(portfolio: Portfolio) -> List[int]:
    return sorted({a.year for a in portfolio.actuals})
