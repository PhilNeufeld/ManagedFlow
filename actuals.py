"""
actuals.py

Bring in what really happened (actuals) and near-term forecasts, and compare
them with the plan.

1. parse_csv()   reads the agreed CSV layout:
       year,kind,name,category,direction,amount,month,entity,asset,notes[,status,class]
2. match_rows()  finds the planned Cashflow each row measures (by asset and
                 name, then by asset and category) -- you review the matches
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

from models import ACTUAL_STATUSES, Actual, CashflowDirection, FlowType, Portfolio, ValidationError

CSV_COLUMNS = ["year", "kind", "name", "category", "direction", "amount", "month", "entity", "asset", "notes",
               "status", "class"]   # status and class are optional

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
    "2026,actual,Pistachio crop sales,crop_sales,inflow,118500,,Branfeld Investments,Pistachios,Lower yield than planned,confirmed,Crop 2026\n"
    "2027,forecast,Pistachio crop sales,crop_sales,inflow,132000,,Branfeld Investments,Pistachios,,estimated,Crop 2027\n"
    "2026,actual,Brix rent,rent,inflow,1150,3,Branfeld Investments,4392 Brix,,provisional,\n"
)


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
    # filled in by match_rows()
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
    missing = [c for c in ("year", "kind", "name", "category", "direction", "amount") if c not in headers]
    if missing:
        raise ValueError(f"CSV is missing column(s): {', '.join(missing)}. Expected: {','.join(CSV_COLUMNS)}")

    rows = []
    for i, raw in enumerate(reader, start=2):  # line 1 is the header
        # Extra trailing commas put spare values under a None key -- ignore them.
        r = {k.strip().lower(): (v if isinstance(v, str) else "").strip() for k, v in raw.items() if k}
        if not any(r.values()):
            continue
        row = ImportRow(line=i, name=r.get("name", ""), notes=r.get("notes", ""),
                        entity_text=r.get("entity", ""), asset_text=r.get("asset", ""))
        try:
            row.year = int(r.get("year", ""))
        except ValueError:
            row.errors.append(f"year '{r.get('year')}' isn't a year")
        row.kind = r.get("kind", "").lower()
        if row.kind not in ("actual", "forecast"):
            row.errors.append(f"kind must be actual or forecast, not '{row.kind}'")
        category = r.get("category", "").lower()
        row.category = CATEGORY_ALIASES.get(category, category)
        if row.category not in {t.value for t in FlowType}:
            row.errors.append(f"unknown category '{category}'")
        row.direction = r.get("direction", "").lower()
        if row.direction not in ("inflow", "outflow"):
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


def match_rows(rows: List[ImportRow], portfolio: Portfolio) -> List[ImportRow]:
    """Find the asset, entity and planned cashflow for each row."""
    assets = {_norm(a.name): a.asset_id for a in portfolio.assets}
    entities = {_norm(e.name): e.entity_id for e in portfolio.entities}
    for row in rows:
        if row.asset_text:
            row.asset_id = assets.get(_norm(row.asset_text))
            if row.asset_id is None:
                row.match_note = f"asset '{row.asset_text}' not found; "
        if row.entity_text:
            row.entity_id = entities.get(_norm(row.entity_text))
            if row.entity_id is None:
                row.match_note += f"entity '{row.entity_text}' not found; "
        candidates = [cf for cf in portfolio.cashflows if cf.asset_id == row.asset_id]
        by_name = [cf for cf in candidates if _norm(cf.name) == _norm(row.name)]
        by_category = [cf for cf in candidates
                       if cf.flow_type.value == row.category and cf.direction.value == row.direction]
        if len(by_name) == 1:
            row.flow_id = by_name[0].flow_id
            row.match_note += "matched by name"
        elif len(by_category) == 1:
            row.flow_id = by_category[0].flow_id
            row.match_note += f"matched by category to '{by_category[0].name}'"
        else:
            row.match_note += ("several possible cashflows -- pick one" if len(by_category) > 1
                               else "not in the plan")
    return rows


def commit_rows(rows: List[ImportRow], portfolio: Portfolio) -> int:
    """Save valid rows as Actual records. Returns how many were saved."""
    saved = 0
    for row in rows:
        if not row.ok:
            continue
        actual_id = f"actual_{row.year}_{row.month or 0}_{row.kind}_{_norm(row.name)[:30]}_{saved}"
        while any(a.actual_id == actual_id for a in portfolio.actuals):
            actual_id += "_"
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
