"""
exports.py

Excel export/reporting layer. Excel here is a reporting and review surface,
not the calculation engine -- every number written to the workbook is
computed by Python (models.py / simulation.py / taxes.py) and written as a
value, not as a live Excel formula. This keeps a single source of truth
for the math and avoids drift between "what the model says" and "what the
spreadsheet says."

Workbook tabs:
    1. Inputs              - quick-reference summary of household/assumptions
    2. Assumptions          - assumptions + default tax rates
    3. Entities             - legal/tax holding structures (Personal, LLC, Trust, ...)
    4. Assets               - one row per asset
    5. Cashflows            - one row per cashflow (asset + household level)
    6. Bundles              - named groupings of Assets/Cashflows under an Entity
    7. Scenario Settings    - one row per scenario + its overrides
    8. Annual Results       - one row per (scenario, year) household summary
    9. Asset Detail         - one row per (scenario, year, asset)
    10. Cashflow Detail     - one row per (scenario, year, cashflow) with amount
    11. Tax Estimate        - one row per (scenario, year) tax breakdown by category
    12. Drawdowns           - one row per (scenario, year, asset) withdrawal
    13. Charts              - net worth and income/expense charts per scenario
    14. Notes / Warnings    - any warnings raised during simulation
"""

from __future__ import annotations

import actuals as actuals_engine
import explorer
import rentals

from pathlib import Path
from typing import Dict, List

from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from models import Portfolio, SimulationResult


# ---------------------------------------------------------------------------
# Shared styling
# ---------------------------------------------------------------------------

FONT_NAME = "Calibri"
HEADER_FILL = PatternFill("solid", start_color="1F4E78", end_color="1F4E78")
HEADER_FONT = Font(name=FONT_NAME, bold=True, color="FFFFFF")
TITLE_FONT = Font(name=FONT_NAME, bold=True, size=14)
SUBTITLE_FONT = Font(name=FONT_NAME, italic=True, size=10, color="555555")
INPUT_FONT = Font(name=FONT_NAME, color="0000FF")  # blue = hardcoded input, per convention
WARNING_FILL = PatternFill("solid", start_color="FFF2CC", end_color="FFF2CC")
NEGATIVE_FILL = PatternFill("solid", start_color="FCE4E4", end_color="FCE4E4")

CURRENCY_FORMAT = '$#,##0;[RED]($#,##0)'
PERCENT_FORMAT = '0.0%'


def _style_header_row(ws: Worksheet, row: int, num_cols: int) -> None:
    for col in range(1, num_cols + 1):
        cell = ws.cell(row=row, column=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")


def _write_table(
    ws: Worksheet,
    start_row: int,
    headers: List[str],
    rows: List[list],
    currency_cols: List[int] | None = None,
    percent_cols: List[int] | None = None,
) -> int:
    """Write a header row + data rows starting at start_row (1-indexed).
    currency_cols / percent_cols are 0-indexed column positions within the
    row to apply number formatting to. Returns the row number after the
    last written row.
    """
    currency_cols = currency_cols or []
    percent_cols = percent_cols or []

    for col_idx, header in enumerate(headers, start=1):
        ws.cell(row=start_row, column=col_idx, value=header)
    _style_header_row(ws, start_row, len(headers))

    for r, row_data in enumerate(rows, start=start_row + 1):
        for c, value in enumerate(row_data, start=1):
            cell = ws.cell(row=r, column=c, value=value)
            cell.font = Font(name=FONT_NAME)
            zero_indexed = c - 1
            if zero_indexed in currency_cols:
                cell.number_format = CURRENCY_FORMAT
            elif zero_indexed in percent_cols:
                cell.number_format = PERCENT_FORMAT

    ws.freeze_panes = ws.cell(row=start_row + 1, column=1)
    for col_idx in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = 20

    last_row = start_row + len(rows)
    if rows:
        ws.auto_filter.ref = (
            f"{get_column_letter(1)}{start_row}:{get_column_letter(len(headers))}{last_row}"
        )
    return last_row + 1


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def export_to_excel(
    portfolio: Portfolio,
    simulation_results: List[SimulationResult],
    output_path: str | Path,
) -> Path:
    """Build and save the full Excel workbook. `simulation_results` should
    be the output of simulation.run_all_scenarios(portfolio) (or a manually
    assembled list of one-or-more SimulationResult objects).
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    wb.remove(wb.active)  # we'll create named sheets explicitly, in order

    _write_inputs_sheet(wb, portfolio)
    _write_assumptions_sheet(wb, portfolio)
    _write_entities_sheet(wb, portfolio)
    _write_ownership_sheet(wb, portfolio)
    _write_assets_sheet(wb, portfolio)
    _write_cashflows_sheet(wb, portfolio)
    _write_liabilities_sheet(wb, portfolio)
    _write_bundles_sheet(wb, portfolio)
    _write_scenario_settings_sheet(wb, portfolio)
    _write_annual_results_sheet(wb, simulation_results)
    _write_cashflow_summary_sheet(wb, portfolio, simulation_results)
    _write_asset_detail_sheet(wb, simulation_results)
    _write_liability_detail_sheet(wb, simulation_results)
    _write_cashflow_detail_sheet(wb, portfolio, simulation_results)
    _write_tax_estimate_sheet(wb, simulation_results)
    _write_tax_detail_sheet(wb, simulation_results)
    _write_drawdowns_sheet(wb, simulation_results)
    _write_charts_sheet(wb, simulation_results)
    _write_actuals_sheets(wb, portfolio)
    _write_rental_pro_forma_sheet(wb, portfolio)
    _write_period_view_sheet(wb, portfolio)
    _write_audit_sheets(wb, portfolio, simulation_results)
    _write_notes_warnings_sheet(wb, simulation_results)

    wb.save(output_path)
    return output_path


# ---------------------------------------------------------------------------
# 1. Inputs
# ---------------------------------------------------------------------------

def _write_inputs_sheet(wb: Workbook, portfolio: Portfolio) -> None:
    ws = wb.create_sheet("Inputs")
    ws["A1"] = "Household Financial Plan - Inputs Summary"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "Generated by ManagedFlow. Excel is a reporting layer; Python is the calculation engine."
    ws["A2"].font = SUBTITLE_FONT

    rows = [
        ["Household name", portfolio.household.name],
        ["Members", ", ".join(portfolio.household.members)],
        ["Planning start year", portfolio.assumptions.start_year],
        ["Planning end year", portfolio.assumptions.end_year],
        ["Inflation rate assumption", portfolio.assumptions.inflation_rate],
        ["Number of entities", len(portfolio.entities)],
        ["Number of assets", len(portfolio.assets)],
        ["Number of household-level cashflows", len(portfolio.household_level_cashflows())],
        ["Number of bundles", len(portfolio.bundles)],
        ["Number of scenarios defined", len(portfolio.scenarios)],
        ["File version", portfolio.file_version],
        ["Last updated", portfolio.updated_at],
    ]
    row_num = 4
    for label, value in rows:
        ws.cell(row=row_num, column=1, value=label).font = Font(name=FONT_NAME, bold=True)
        cell = ws.cell(row=row_num, column=2, value=value)
        cell.font = INPUT_FONT
        if label == "Inflation rate assumption":
            cell.number_format = PERCENT_FORMAT
        row_num += 1

    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 40


# ---------------------------------------------------------------------------
# 2. Assumptions
# ---------------------------------------------------------------------------

def _write_assumptions_sheet(wb: Workbook, portfolio: Portfolio) -> None:
    ws = wb.create_sheet("Assumptions")
    ws["A1"] = "Assumptions"
    ws["A1"].font = TITLE_FONT

    a = portfolio.assumptions
    headers = ["Assumption", "Value"]
    rows = [
        ["Start year", a.start_year],
        ["End year", a.end_year],
        ["Inflation rate", a.inflation_rate],
    ]
    _write_table(ws, 3, headers, rows, percent_cols=[1])
    ws.cell(row=3 + len(rows) + 2, column=1, value="Default tax rates by category").font = Font(
        name=FONT_NAME, bold=True
    )
    tax_rows = [[k, v] for k, v in sorted(a.default_tax_rates.items())]
    _write_table(ws, 3 + len(rows) + 3, ["Tax category", "Rate"], tax_rows, percent_cols=[1])


# ---------------------------------------------------------------------------
# 2b. Entities
# ---------------------------------------------------------------------------

ENTITY_HEADERS = ["Entity ID", "Name", "Entity Type", "Notes"]


def _entity_row(entity) -> list:
    return [entity.entity_id, entity.name, entity.entity_type.value, entity.notes]


def _write_entities_sheet(wb: Workbook, portfolio: Portfolio) -> None:
    ws = wb.create_sheet("Entities")
    ws["A1"] = "Entities (legal / tax holding structures)"
    ws["A1"].font = TITLE_FONT
    rows = [_entity_row(e) for e in portfolio.entities]
    _write_table(ws, 3, ENTITY_HEADERS, rows)


# ---------------------------------------------------------------------------
# 2c. Bundles
# ---------------------------------------------------------------------------

BUNDLE_HEADERS = ["Bundle ID", "Name", "Entity", "Assets", "Household Cashflows", "Notes"]


def _bundle_row(bundle, entity_name_by_id: Dict[str, str], asset_name_by_id: Dict[str, str],
                 flow_name_by_id: Dict[str, str]) -> list:
    entity_label = entity_name_by_id.get(bundle.entity_id, bundle.entity_id)
    asset_labels = ", ".join(asset_name_by_id.get(aid, aid) for aid in bundle.asset_ids)
    flow_labels = ", ".join(flow_name_by_id.get(fid, fid) for fid in bundle.cashflow_ids)
    return [bundle.bundle_id, bundle.name, entity_label, asset_labels, flow_labels, bundle.notes]


def _write_bundles_sheet(wb: Workbook, portfolio: Portfolio) -> None:
    ws = wb.create_sheet("Bundles")
    ws["A1"] = "Bundles (Assets + Cashflows grouped under an Entity)"
    ws["A1"].font = TITLE_FONT
    entity_name_by_id = {e.entity_id: e.name for e in portfolio.entities}
    asset_name_by_id = {a.asset_id: a.name for a in portfolio.assets}
    flow_name_by_id = {cf.flow_id: cf.name for cf in portfolio.cashflows}
    rows = [
        _bundle_row(b, entity_name_by_id, asset_name_by_id, flow_name_by_id)
        for b in portfolio.bundles
    ]
    _write_table(ws, 3, BUNDLE_HEADERS, rows)
    ws.column_dimensions["D"].width = 36
    ws.column_dimensions["E"].width = 36


# ---------------------------------------------------------------------------
# 3. Assets
# ---------------------------------------------------------------------------

ASSET_HEADERS = [
    "Asset ID", "Name", "Type", "Owner", "Entity", "Initial Value", "Current Value",
    "Start Year", "Annual Growth Rate", "Liquidity Category", "Tax Category",
    "Active", "Do Not Sell", "Sale Year", "Currency", "Notes",
]


def _asset_row(asset, entity_name_by_id: Dict[str, str]) -> list:
    entity_label = entity_name_by_id.get(asset.entity_id, asset.entity_id or "")
    return [
        asset.asset_id, asset.name, asset.asset_type.value, asset.owner, entity_label,
        asset.initial_value, asset.current_value, asset.start_year, asset.annual_growth_rate,
        asset.liquidity_category.value, asset.tax_category.value, asset.active,
        asset.do_not_sell, asset.sale_year, getattr(asset, "currency", "USD"), asset.notes,
    ]


def _write_assets_sheet(wb: Workbook, portfolio: Portfolio) -> None:
    ws = wb.create_sheet("Assets")
    ws["A1"] = "Assets"
    ws["A1"].font = TITLE_FONT
    entity_name_by_id = {e.entity_id: e.name for e in portfolio.entities}
    rows = [_asset_row(a, entity_name_by_id) for a in portfolio.assets]
    _write_table(ws, 3, ASSET_HEADERS, rows, currency_cols=[5, 6], percent_cols=[8])


# ---------------------------------------------------------------------------
# 4. Cashflows
# ---------------------------------------------------------------------------

CASHFLOW_HEADERS = [
    "Flow ID", "Asset ID", "Name", "Flow Type", "Direction", "Amount", "Start Year",
    "End Year", "Frequency", "Annual Growth Rate", "Inflation Adjusted", "Taxable",
    "Tax Category", "Affects Asset Value", "Affects Debt Balance", "Notes",
]


def _cashflow_row(cf) -> list:
    return [
        cf.flow_id, cf.asset_id or "(household)", cf.name, cf.flow_type.value, cf.direction.value,
        cf.amount, cf.start_year, cf.end_year, cf.frequency.value, cf.annual_growth_rate,
        cf.inflation_adjusted, cf.taxable, cf.tax_category.value, cf.affects_asset_value,
        cf.affects_debt_balance, cf.notes,
    ]


def _write_cashflows_sheet(wb: Workbook, portfolio: Portfolio) -> None:
    ws = wb.create_sheet("Cashflows")
    ws["A1"] = "Cashflows (asset-level and household-level)"
    ws["A1"].font = TITLE_FONT
    rows = [_cashflow_row(cf) for cf in portfolio.all_cashflows()]
    _write_table(ws, 3, CASHFLOW_HEADERS, rows, currency_cols=[5], percent_cols=[9])


# ---------------------------------------------------------------------------
# 5. Scenario Settings
# ---------------------------------------------------------------------------

def _write_scenario_settings_sheet(wb: Workbook, portfolio: Portfolio) -> None:
    ws = wb.create_sheet("Scenario Settings")
    ws["A1"] = "Scenario Settings"
    ws["A1"].font = TITLE_FONT

    entity_name_by_id = {e.entity_id: e.name for e in portfolio.entities}
    bundle_name_by_id = {b.bundle_id: b.name for b in portfolio.bundles}

    headers = [
        "Scenario ID", "Name", "Base Case?", "Scope", "Inflation Override",
        "Allow Illiquid Withdrawals", "Reinvest Surplus", "Asset Overrides",
        "Cashflow Overrides", "Description",
    ]
    rows = []
    for s in portfolio.scenarios:
        rows.append([
            s.scenario_id, s.name, s.is_base_case,
            _format_scope(s, entity_name_by_id, bundle_name_by_id),
            s.inflation_rate_override,
            s.allow_illiquid_withdrawals, s.reinvest_surplus,
            _format_overrides(s.asset_overrides), _format_overrides(s.cashflow_overrides),
            s.description,
        ])
    _write_table(ws, 3, headers, rows, percent_cols=[4])
    ws.column_dimensions["D"].width = 36
    ws.column_dimensions["H"].width = 45
    ws.column_dimensions["I"].width = 45
    ws.column_dimensions["J"].width = 50


def _format_scope(scenario, entity_name_by_id: Dict[str, str], bundle_name_by_id: Dict[str, str]) -> str:
    if not scenario.entity_ids and not scenario.bundle_ids:
        return "Full Portfolio"
    parts = []
    if scenario.entity_ids:
        labels = ", ".join(entity_name_by_id.get(eid, eid) for eid in scenario.entity_ids)
        parts.append(f"Entities: {labels}")
    if scenario.bundle_ids:
        labels = ", ".join(bundle_name_by_id.get(bid, bid) for bid in scenario.bundle_ids)
        parts.append(f"Bundles: {labels}")
    return " | ".join(parts)


def _format_overrides(overrides: Dict[str, Dict]) -> str:
    if not overrides:
        return ""
    parts = []
    for key, vals in overrides.items():
        vals_str = "; ".join(f"{k}={v}" for k, v in vals.items())
        parts.append(f"{key}: {vals_str}")
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# 6. Annual Results
# ---------------------------------------------------------------------------

ANNUAL_RESULTS_HEADERS = [
    "Scenario", "Year", "Total Gross Income", "Total Expenses", "Estimated Tax",
    "Net Cashflow", "Surplus / (Deficit)", "Withdrawal Amount", "Reinvestment Amount",
    "Total Net Worth", "Liquid Assets", "Illiquid Assets",
    "Total Assets", "Total Liabilities", "Debt Service", "Interest Paid", "Ages",
]


def _write_annual_results_sheet(wb: Workbook, simulation_results: List[SimulationResult]) -> None:
    ws = wb.create_sheet("Annual Results")
    ws["A1"] = "Annual Household Results by Scenario"
    ws["A1"].font = TITLE_FONT

    rows = []
    for sim in simulation_results:
        for r in sim.annual_results:
            rows.append([
                sim.scenario_id, r.year, r.total_gross_income, r.total_expenses,
                r.total_estimated_tax, r.net_cashflow, r.surplus_or_deficit,
                r.withdrawal_amount, r.reinvestment_amount, r.total_net_worth,
                r.liquid_net_worth, r.illiquid_net_worth,
                r.total_assets, r.total_liabilities, r.total_debt_service, r.total_interest_paid,
                ", ".join(f"{n} {a}" for n, a in r.ages.items()),
            ])
    _write_table(ws, 3, ANNUAL_RESULTS_HEADERS, rows, currency_cols=list(range(2, 16)))

    # Highlight deficit years for quick visual scanning.
    deficit_col = ANNUAL_RESULTS_HEADERS.index("Surplus / (Deficit)") + 1
    for r_idx, row_data in enumerate(rows, start=4):
        if row_data[deficit_col - 1] < 0:
            ws.cell(row=r_idx, column=deficit_col).fill = NEGATIVE_FILL


# ---------------------------------------------------------------------------
# 7. Asset Detail
# ---------------------------------------------------------------------------

ASSET_DETAIL_HEADERS = [
    "Scenario", "Year", "Asset ID", "Beginning Value", "Growth Amount",
    "Net Cashflow Affecting Value", "Ending Value", "Gross Income", "Gross Expense",
    "Sold This Year", "Sale Proceeds",
]


def _write_asset_detail_sheet(wb: Workbook, simulation_results: List[SimulationResult]) -> None:
    ws = wb.create_sheet("Asset Detail")
    ws["A1"] = "Per-Asset Annual Detail by Scenario"
    ws["A1"].font = TITLE_FONT

    rows = []
    for sim in simulation_results:
        for r in sim.annual_results:
            for ar in r.asset_results:
                rows.append([
                    sim.scenario_id, ar.year, ar.asset_id, ar.beginning_value, ar.growth_amount,
                    ar.net_cashflow_affecting_value, ar.ending_value, ar.gross_income,
                    ar.gross_expense, ar.sold_this_year, ar.sale_proceeds,
                ])
    _write_table(ws, 3, ASSET_DETAIL_HEADERS, rows, currency_cols=list(range(3, 9)) + [10])


def _write_period_view_sheet(wb: Workbook, portfolio: Portfolio, years: int = 5) -> None:
    """Whole plan, base scenario, first `years` years: one row per asset / liability /
    cashflow, grouped, with events and substantial-change flags."""
    scenario = next((s for s in portfolio.scenarios if s.is_base_case), portfolio.scenarios[0] if portfolio.scenarios else None)
    if scenario is None:
        return
    result = explorer.run(portfolio, scenario)
    nodes = explorer.build_tree(result.working_portfolio)   # includes unplanned actuals
    pv = explorer.period_view(portfolio, nodes, explorer.ROOT, result, portfolio.assumptions.start_year, years)
    yrs = pv["years"]
    ws = wb.create_sheet("Period View")
    ws["A1"] = f"Period view {yrs[0]}-{yrs[-1]} -- scenario '{scenario.name}' (your share)"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = ("Assets = year-end value; liabilities = balance; cashflows = the year's amount (outflows negative). "
                "▲/▼ = changed from the year before by at least 10% and $5,000.")
    ws["A2"].font = SUBTITLE_FONT
    headers = ["Item", "Class"] + [str(y) for y in yrs] + ["", "Change", "Events / notes"]
    hr = 4
    for c, h in enumerate(headers, start=1):
        ws.cell(row=hr, column=c, value=h)
    _style_header_row(ws, hr, len(headers))
    row = hr + 1
    ws.cell(row=row, column=1, value="Events").font = Font(name=FONT_NAME, bold=True)
    for i, y in enumerate(yrs):
        cell = ws.cell(row=row, column=3 + i, value="\n".join(pv["events"][y]) or "-")
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        cell.font = Font(name=FONT_NAME, size=9)
    row += 1
    flag_col = 3 + len(yrs)
    for r in pv["rows"]:
        if r["type"] == "group":
            row += 1
            ws.cell(row=row, column=1, value=r["label"]).font = Font(name=FONT_NAME, bold=True, size=11)
            row += 1
            continue
        bold = r["type"] in ("subtotal", "total")
        ws.cell(row=row, column=1, value=("    " * r.get("indent", 0)) + r["label"]).font = Font(name=FONT_NAME, bold=bold)
        if r["type"] == "item":
            ws.cell(row=row, column=2, value=r["kind"]).font = Font(name=FONT_NAME)
        flags = []
        for i, y in enumerate(yrs):
            cell = ws.cell(row=row, column=3 + i, value=round(r["values"][y], 2))
            cell.number_format = CURRENCY_FORMAT
            cell.font = Font(name=FONT_NAME, bold=bold)
            f = r.get("flags", {}).get(y, "")
            if f:
                flags.append(f"{y}{f}")
        ws.cell(row=row, column=flag_col, value=" ".join(flags)).font = Font(name=FONT_NAME)
        c = ws.cell(row=row, column=flag_col + 1, value=round(r["change"], 2))
        c.number_format = CURRENCY_FORMAT
        c.font = Font(name=FONT_NAME, bold=bold)
        ws.cell(row=row, column=flag_col + 2, value=r.get("note", "")).font = Font(name=FONT_NAME, size=9)
        row += 1
    ws.column_dimensions["A"].width = 46
    ws.column_dimensions["B"].width = 11
    for i in range(len(yrs)):
        ws.column_dimensions[get_column_letter(3 + i)].width = 15
    ws.column_dimensions[get_column_letter(flag_col)].width = 14
    ws.column_dimensions[get_column_letter(flag_col + 1)].width = 15
    ws.column_dimensions[get_column_letter(flag_col + 2)].width = 70
    ws.row_dimensions[hr + 1].height = 60
    ws.freeze_panes = ws.cell(row=hr + 1, column=3)


def _write_rental_pro_forma_sheet(wb: Workbook, portfolio: Portfolio) -> None:
    """One block per rental (base scenario): lines down the side, years across."""
    rental_list = rentals.rental_assets(portfolio)
    scenario = next((s for s in portfolio.scenarios if s.is_base_case), portfolio.scenarios[0] if portfolio.scenarios else None)
    if not rental_list or scenario is None:
        return
    ws = wb.create_sheet("Rental Pro Forma")
    ws["A1"] = f"Rental pro forma -- scenario '{scenario.name}' (your share)"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "GOI = gross rent - vacancy. NOI = GOI - operating expenses. Cash flow = NOI - interest - principal."
    ws["A2"].font = SUBTITLE_FONT
    row = 4
    for asset in rental_list:
        data = rentals.rental_pro_forma(portfolio, scenario, asset.asset_id)
        if not data:
            continue
        lines = []
        for r in data:
            lines += [k for k in r if k != "Year" and k not in lines]
        ws.cell(row=row, column=1, value=asset.name).font = Font(name=FONT_NAME, bold=True, size=12)
        row += 1
        headers = ["Line"] + [str(r["Year"]) for r in data]
        body = [[line] + [r.get(line) for r in data] for line in lines]
        for c, h in enumerate(headers, start=1):
            ws.cell(row=row, column=c, value=h)
        _style_header_row(ws, row, len(headers))
        for i, values in enumerate(body, start=1):
            for c, v in enumerate(values, start=1):
                cell = ws.cell(row=row + i, column=c, value=v)
                cell.font = Font(name=FONT_NAME, bold=values[0] in (
                    "Gross operating income (GOI)", "Net operating income (NOI)", "Cash flow before tax",
                    "Cash flow after tax"))
                if c > 1:
                    cell.number_format = '0.00"x"' if values[0].startswith("Debt coverage") else CURRENCY_FORMAT
        row += len(body) + 2
    ws.column_dimensions["A"].width = 44
    for c in range(2, 60):
        ws.column_dimensions[get_column_letter(c)].width = 13
    ws.freeze_panes = "B4"


def _write_actuals_sheets(wb: Workbook, portfolio: Portfolio) -> None:
    """Actuals (raw records), Plan vs Actual and Trend Breaks, one block per year."""
    if not portfolio.actuals:
        return
    names = {cf.flow_id: cf.name for cf in portfolio.cashflows}
    ws = wb.create_sheet("Actuals")
    ws["A1"] = "Recorded actuals and forecasts (your share)"
    ws["A1"].font = TITLE_FONT
    rows = [[a.year, a.month or "", a.kind, a.name, a.direction.value, a.amount, a.status, a.record_class,
             names.get(a.flow_id, "(not in plan)"), a.notes]
            for a in sorted(portfolio.actuals, key=lambda a: (a.year, a.month or 0, a.name))]
    _write_table(ws, 3, ["Year", "Month", "Kind", "Item", "Direction", "Amount", "Status", "Class",
                         "Planned Cashflow", "Notes"],
                 rows, currency_cols=[5])

    ws = wb.create_sheet("Plan vs Actual")
    ws["A1"] = "Plan vs actual (your share); Flag = at least 10% and $1,000 different"
    ws["A1"].font = TITLE_FONT
    rows = []
    for year in actuals_engine.years_with_records(portfolio):
        for r in actuals_engine.plan_vs_actual(portfolio, year):
            rows.append([year, r["Item"], r["Direction"], r["Planned"], r["Actual / forecast"], r["Source"],
                         r["Difference"], r["Difference %"], "FLAG" if r["Flag"] else ""])
    _write_table(ws, 3, ["Year", "Item", "Direction", "Planned", "Actual / Forecast", "Source", "Difference",
                         "Difference %", "Flag"], rows, currency_cols=[3, 4, 6], percent_cols=[7])

    ws = wb.create_sheet("Trend Breaks")
    ws["A1"] = "Does each year continue the line's own path? (expected = prior year grown at the line's rate)"
    ws["A1"].font = TITLE_FONT
    rows = []
    for year in actuals_engine.years_with_records(portfolio):
        for r in actuals_engine.trend_breaks(portfolio, year):
            rows.append([year, r["Item"], r["Prior year"], r["Prior year is"], r["Growth used"], r["Expected"],
                         r["This year"], r["This year is"], r["Gap"], r["Gap %"], r["Plan for year"],
                         "FLAG" if r["Flag"] else ""])
    _write_table(ws, 3, ["Year", "Item", "Prior Year", "Prior Year Is", "Growth Used", "Expected", "This Year",
                         "This Year Is", "Gap", "Gap %", "Plan for Year", "Flag"], rows,
                 currency_cols=[2, 5, 6, 8, 10], percent_cols=[4, 9])


def _write_ownership_sheet(wb: Workbook, portfolio: Portfolio) -> None:
    ws = wb.create_sheet("Ownership")
    ws["A1"] = "Ownership % (your share = percents multiplied along each path, added across paths)"
    ws["A1"].font = TITLE_FONT
    names = {e.entity_id: e.name for e in portfolio.entities}
    names.update({a.asset_id: a.name for a in portfolio.assets})
    rows = [[names.get(o.owner_entity_id, o.owner_entity_id), names.get(o.owned_id, o.owned_id), o.owned_type,
             o.percent, portfolio.entity_share(o.owned_id) if o.owned_type == "entity" else portfolio.asset_share(o.owned_id),
             o.notes] for o in portfolio.ownerships]
    _write_table(ws, 3, ["Owner", "Owns", "Kind", "Percent", "Your Share of It", "Notes"], rows,
                 percent_cols=[3, 4])


# ---------------------------------------------------------------------------
# Liabilities (inputs) and Liability Detail (per-year results)
# ---------------------------------------------------------------------------

LIABILITY_HEADERS = [
    "Liability ID", "Name", "Kind", "Type", "Lender", "Entity", "Secured By",
    "Balance", "Interest Rate", "Start Year", "Term (months)", "Monthly Payment",
    "Credit Limit", "Active", "Notes",
]


def _write_liabilities_sheet(wb: Workbook, portfolio: Portfolio) -> None:
    ws = wb.create_sheet("Liabilities")
    ws["A1"] = "Liabilities"
    ws["A1"].font = TITLE_FONT
    entity_name_by_id = {e.entity_id: e.name for e in portfolio.entities}
    asset_name_by_id = {a.asset_id: a.name for a in portfolio.assets}
    rows = []
    for li in portfolio.liabilities:
        payment = li.scheduled_monthly_payment() if hasattr(li, "scheduled_monthly_payment") else None
        rows.append([
            li.liability_id, li.name, li.kind, li.liability_type.value, li.lender,
            entity_name_by_id.get(li.entity_id, li.entity_id or ""),
            asset_name_by_id.get(li.secured_by_asset_id, li.secured_by_asset_id or ""),
            li.balance, li.annual_interest_rate, li.start_year,
            getattr(li, "term_months", None), payment, getattr(li, "credit_limit", None),
            li.active, li.notes,
        ])
    _write_table(ws, 3, LIABILITY_HEADERS, rows, currency_cols=[7, 11, 12], percent_cols=[8])


LIABILITY_DETAIL_HEADERS = [
    "Scenario", "Year", "Liability ID", "Beginning Balance", "Interest Paid",
    "Principal Paid", "Debt Service", "Ending Balance", "Paid Off This Year",
]


def _write_liability_detail_sheet(wb: Workbook, simulation_results: List[SimulationResult]) -> None:
    ws = wb.create_sheet("Liability Detail")
    ws["A1"] = "Per-Liability Annual Detail by Scenario"
    ws["A1"].font = TITLE_FONT
    rows = []
    for sim in simulation_results:
        for r in sim.annual_results:
            for lr in r.liability_results:
                rows.append([
                    sim.scenario_id, lr.year, lr.liability_id, lr.beginning_balance,
                    lr.interest_paid, lr.principal_paid, lr.debt_service, lr.ending_balance,
                    lr.paid_off_this_year,
                ])
    _write_table(ws, 3, LIABILITY_DETAIL_HEADERS, rows, currency_cols=[3, 4, 5, 6, 7])


# ---------------------------------------------------------------------------
# 8. Cashflow Detail
# ---------------------------------------------------------------------------

CASHFLOW_DETAIL_HEADERS = ["Scenario", "Year", "Flow ID", "Asset ID", "Name", "Flow Type", "Direction", "Amount"]


def _write_cashflow_detail_sheet(
    wb: Workbook, portfolio: Portfolio, simulation_results: List[SimulationResult]
) -> None:
    ws = wb.create_sheet("Cashflow Detail")
    ws["A1"] = "Per-Cashflow Annual Amounts by Scenario"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "Note: amounts reflect each cashflow's own start/end/growth; they do not re-apply scenario overrides to amount unless the override targeted that flow."
    ws["A2"].font = SUBTITLE_FONT

    all_flows = portfolio.all_cashflows()
    rows = []
    for sim in simulation_results:
        years = [r.year for r in sim.annual_results]
        for cf in all_flows:
            for year in years:
                amount = cf.signed_amount_in_year(year)
                if amount == 0 and (year < cf.start_year or (cf.end_year is not None and year > cf.end_year)):
                    continue
                rows.append([
                    sim.scenario_id, year, cf.flow_id, cf.asset_id or "(household)",
                    cf.name, cf.flow_type.value, cf.direction.value, amount,
                ])
    _write_table(ws, 4, CASHFLOW_DETAIL_HEADERS, rows, currency_cols=[7])


# ---------------------------------------------------------------------------
# 9. Tax Estimate
# ---------------------------------------------------------------------------

def _write_tax_detail_sheet(wb: Workbook, simulation_results: List[SimulationResult]) -> None:
    """Line-by-line detailed tax, one row per scenario and year (detailed method only)."""
    rows, keys = [], None
    for sim in simulation_results:
        for r in sim.annual_results:
            if r.tax_estimate and r.tax_estimate.details:
                keys = keys or list(r.tax_estimate.details)
                rows.append([sim.scenario_id, r.year] + [r.tax_estimate.details.get(k, 0.0) for k in keys])
    if not rows:
        return
    ws = wb.create_sheet("Tax Detail")
    ws["A1"] = "Detailed tax by year (federal 2026 / California 2025 tables, indexed by inflation)"
    ws["A1"].font = TITLE_FONT
    rate_col = keys.index("Effective rate on AGI") + 2
    _write_table(ws, 3, ["Scenario", "Year"] + keys, rows,
                 currency_cols=[i for i in range(2, len(keys) + 2) if i != rate_col], percent_cols=[rate_col])


def _write_tax_estimate_sheet(wb: Workbook, simulation_results: List[SimulationResult]) -> None:
    ws = wb.create_sheet("Tax Estimate")
    ws["A1"] = "Estimated Tax by Category and Year"
    ws["A1"].font = TITLE_FONT

    headers = ["Scenario", "Year", "Tax Category", "Taxable Income", "Estimated Tax"]
    rows = []
    for sim in simulation_results:
        for r in sim.annual_results:
            if not r.tax_estimate:
                continue
            for category, income in r.tax_estimate.taxable_income_by_category.items():
                tax = r.tax_estimate.tax_by_category.get(category, 0.0)
                rows.append([sim.scenario_id, r.year, category, income, tax])
            if r.tax_estimate.details:  # detailed method: list each tax too
                for kind, tax in r.tax_estimate.tax_by_category.items():
                    rows.append([sim.scenario_id, r.year, f"tax: {kind}", None, tax])
    _write_table(ws, 3, headers, rows, currency_cols=[3, 4])


# ---------------------------------------------------------------------------
# 10. Drawdowns
# ---------------------------------------------------------------------------

def _write_drawdowns_sheet(wb: Workbook, simulation_results: List[SimulationResult]) -> None:
    ws = wb.create_sheet("Drawdowns")
    ws["A1"] = "Withdrawal Drawdowns by Scenario / Year / Asset"
    ws["A1"].font = TITLE_FONT

    headers = ["Scenario", "Year", "Asset ID", "Amount Withdrawn"]
    rows = []
    for sim in simulation_results:
        for r in sim.annual_results:
            for asset_id, amount in r.withdrawal_breakdown.items():
                rows.append([sim.scenario_id, r.year, asset_id, amount])
    _write_table(ws, 3, headers, rows, currency_cols=[3])


# ---------------------------------------------------------------------------
# 11. Charts
# ---------------------------------------------------------------------------

def _write_charts_sheet(wb: Workbook, simulation_results: List[SimulationResult]) -> None:
    ws = wb.create_sheet("Charts")
    ws["A1"] = "Net Worth by Scenario"
    ws["A1"].font = TITLE_FONT

    if not simulation_results:
        return

    years = [r.year for r in simulation_results[0].annual_results]
    headers = ["Year"] + [sim.scenario_id for sim in simulation_results]
    rows = []
    for i, year in enumerate(years):
        row = [year]
        for sim in simulation_results:
            row.append(sim.annual_results[i].total_net_worth)
        rows.append(row)

    data_start_row = 3
    last_row = _write_table(
        ws, data_start_row, headers, rows,
        currency_cols=list(range(1, len(headers))),
    )

    chart = LineChart()
    chart.title = "Total Net Worth Over Time by Scenario"
    chart.y_axis.title = "Net Worth ($)"
    chart.x_axis.title = "Year"
    chart.width = 26
    chart.height = 14

    data_ref = Reference(
        ws, min_col=2, max_col=len(headers), min_row=data_start_row, max_row=last_row - 1
    )
    cats_ref = Reference(ws, min_col=1, max_col=1, min_row=data_start_row + 1, max_row=last_row - 1)
    chart.add_data(data_ref, titles_from_data=True)
    chart.set_categories(cats_ref)

    anchor_col = len(headers) + 2
    ws.add_chart(chart, f"{get_column_letter(anchor_col)}{data_start_row}")


# ---------------------------------------------------------------------------
# 12. Notes / Warnings
# ---------------------------------------------------------------------------

def _write_audit_sheets(wb: Workbook, portfolio: Portfolio, simulation_results: List[SimulationResult]) -> None:
    """Audit: every item behind each annual total (one row each, filter or pivot it).
    Audit Checks: each total vs. the sum of its items, every year and scenario."""
    import audit
    names = {s.scenario_id: s.name for s in portfolio.scenarios}
    ws = wb.create_sheet("Audit")
    ws["A1"] = "Audit - the items behind each annual total"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = ("One row per item. Sections: Gross income, Expenses (incl. each loan's payments), Debt service "
                "(loan balances, interest, principal), Est. tax (the calculation step by step), Cash (surplus and "
                "where it went), Assets (each asset's roll-forward). Filter by Scenario, Year, Section or Entity.")
    ws["A2"].font = SUBTITLE_FONT
    rows = []
    for res in simulation_results:
        for r in audit.audit_rows(res, portfolio, names.get(res.scenario_id, str(res.scenario_id))):
            rows.append([r[c] for c in audit.COLUMNS])
    _write_table(ws, 4, audit.COLUMNS, rows, currency_cols=[audit.COLUMNS.index("Amount")])
    ws.column_dimensions["D"].width = 48
    ws.column_dimensions["G"].width = 32

    ws = wb.create_sheet("Audit Checks")
    ws["A1"] = "Audit checks - each total vs. the sum of its items"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "OK means they agree within one cent (total assets: within $1, as each asset is rounded to the cent)."
    ws["A2"].font = SUBTITLE_FONT
    headers = ["Scenario", "Year", "Check", "Summary", "Sum of items", "Difference", "OK"]
    rows = []
    for res in simulation_results:
        name = names.get(res.scenario_id, str(res.scenario_id))
        for c in audit.checks(res) + audit.asset_total_check(res, portfolio):
            rows.append([name, c["Year"], c["Check"], c["Summary"], c["Sum of items"], c["Difference"],
                         "OK" if c["OK"] else "CHECK"])
    rows.sort(key=lambda r: (r[0], r[1]))
    _write_table(ws, 4, headers, rows, currency_cols=[3, 4, 5])


def _write_notes_warnings_sheet(wb: Workbook, simulation_results: List[SimulationResult]) -> None:
    ws = wb.create_sheet("Notes - Warnings")
    ws["A1"] = "Simulation Warnings"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "Review these before relying on results for decisions. Empty table means no warnings were raised."
    ws["A2"].font = SUBTITLE_FONT

    headers = ["Scenario", "Year", "Warning"]
    rows = []
    for sim in simulation_results:
        for r in sim.annual_results:
            for w in r.warnings:
                rows.append([sim.scenario_id, r.year, w])
    last_row = _write_table(ws, 4, headers, rows)
    for r_idx in range(5, last_row):
        ws.cell(row=r_idx, column=3).fill = WARNING_FILL
    ws.column_dimensions["C"].width = 90


# ---------------------------------------------------------------------------
# Audit workbook (the Audit tab's download)
# ---------------------------------------------------------------------------

AUDIT_CURRENCY_FORMAT = '$#,##0.00;[RED]($#,##0.00)'


def audit_workbook_bytes(result: SimulationResult, portfolio: Portfolio, years: List[int],
                         entity: str | None = None, scenario_name: str = "") -> bytes:
    """The Audit tab as an Excel workbook: Summary & Checks, one sheet per section
    (items as rows, a column per year, plus Total for flow sections), and All Items
    (one row per item, for filtering and pivots). Amounts to the cent."""
    import io
    import audit

    scenario_name = scenario_name or str(result.scenario_id)
    span = f"{years[0]}" + (f"-{years[-1]}" if len(years) > 1 else "")
    rows = audit.audit_rows(result, portfolio, scenario_name, years)
    wb = Workbook()
    wb.remove(wb.active)

    def title(ws, text, note):
        ws["A1"] = text
        ws["A1"].font = TITLE_FONT
        ws["A2"] = note
        ws["A2"].font = SUBTITLE_FONT

    # Summary & Checks
    ws = wb.create_sheet("Summary & Checks")
    title(ws, f"Audit - {scenario_name}, {span}" + (f", {entity}" if entity else ""),
          "Each total from the Simulation results, then each check: the total vs. the sum of its items.")
    by_year = {r.year: r for r in result.annual_results}
    summary = [("Gross income", "total_gross_income"), ("Expenses (incl. debt service)", "total_expenses"),
               ("Est. tax", "total_estimated_tax"), ("Surplus/(deficit)", "surplus_or_deficit"),
               ("Withdrawal", "withdrawal_amount"), ("Reinvestment", "reinvestment_amount"),
               ("Debt service", "total_debt_service"), ("Total assets", "total_assets"),
               ("Total liabilities", "total_liabilities"), ("Total net worth", "total_net_worth")]
    next_row = _write_table(ws, 4, ["Total"] + [str(y) for y in years],
                            [[label] + [getattr(by_year[y], attr) for y in years] for label, attr in summary],
                            currency_cols=list(range(1, len(years) + 1)))
    checks = (audit.checks(result, years) + audit.asset_total_check(result, portfolio, years)
              + audit.by_group_check(result, audit.asset_statements(result, portfolio, years), years))
    checks.sort(key=lambda c: c["Year"])
    _write_table(ws, next_row + 1, ["Year", "Check", "Summary", "Sum of items", "Difference", "OK"],
                 [[c["Year"], c["Check"], c["Summary"], c["Sum of items"], c["Difference"],
                   "OK" if c["OK"] else "CHECK"] for c in checks], currency_cols=[2, 3, 4])
    ws.freeze_panes = "B5"
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 26

    notes = {
        "Gross income": "Each inflow: household cashflows and each asset's income.",
        "Expenses": "Each outflow, plus each loan's payments (debt service is part of Expenses).",
        "Debt service": "Each loan: beginning balance, interest, principal, ending balance.",
        "Est. tax": "The tax calculation step by step (federal, payroll per person, California).",
        "Cash": "Surplus/(deficit) and where it went: reinvested or withdrawn, by asset.",
        "Assets": "Each asset: beginning value + growth + value-changing cashflows + other changes "
                  "- withdrawn + reinvested = ending value.",
    }
    for section in audit.SECTIONS:
        ent = None if section in ("Est. tax", "Cash") else entity
        df = audit.pivot(rows, section, years, ent)
        ws = wb.create_sheet(section.replace(".", ""))
        title(ws, f"{section} - {scenario_name}, {span}" + (f", {ent}" if ent else ""), notes[section])
        if df.empty:
            ws["A4"] = "Nothing in this section for the years and entity chosen."
            continue
        headers = list(df.columns)
        value_idx = [i for i, c in enumerate(headers) if c in [str(y) for y in years] or c == "Total"]
        data = df.values.tolist()
        last = _write_table(ws, 4, headers, data, currency_cols=value_idx)
        # Rates (e.g. effective tax rate) as percentages, not dollars
        for r_off, row in enumerate(data):
            if row[headers.index("Detail")] == "rate" or "rate" in str(row[headers.index("Line")]).lower():
                for i in value_idx:
                    ws.cell(row=5 + r_off, column=i + 1).number_format = PERCENT_FORMAT
            else:
                for i in value_idx:
                    ws.cell(row=5 + r_off, column=i + 1).number_format = AUDIT_CURRENCY_FORMAT
        if section in audit.FLOW_SECTIONS and section != "Est. tax":
            ws.cell(row=last, column=1, value="Total").font = Font(name=FONT_NAME, bold=True)
            for i in value_idx:
                col = get_column_letter(i + 1)
                cell = ws.cell(row=last, column=i + 1, value=f"=SUM({col}5:{col}{last - 1})")
                cell.number_format = AUDIT_CURRENCY_FORMAT
                cell.font = Font(name=FONT_NAME, bold=True)
        ws.column_dimensions["A"].width = 50
        ws.column_dimensions["C"].width = 32
        ws.freeze_panes = "F5"

    # By Asset: one statement per asset over the years chosen, then each asset year by year
    statements = audit.asset_statements(result, portfolio, years)
    label_fn = audit.group_of(portfolio, result, "Asset")
    ws = wb.create_sheet("By Asset")
    title(ws, f"By asset - {scenario_name}, {span}",
          "Summed over the years chosen. Est. tax is ALLOCATED: each asset's taxable net x the household's average "
          "rate that year; the Household row has the rest, so the Total matches the results.")
    table = audit.by_group(statements, label_fn, years)
    headers = ["Name"] + audit.STATEMENT_LINES
    last = _write_table(ws, 4, headers, [[r[h] for h in headers] for r in table],
                        currency_cols=list(range(1, len(headers))))
    ws.column_dimensions["A"].width = 36
    row = last + 1
    ws.cell(row=row, column=1, value="Each asset, year by year").font = TITLE_FONT
    row += 2
    for r in table:
        if r["Name"] == "Total":
            continue
        ws.cell(row=row, column=1, value=r["Name"]).font = Font(name=FONT_NAME, bold=True)
        stmt = audit.group_statement(statements, label_fn, r["Name"], years)
        row = _write_table(ws, row + 1, ["Line"] + [str(y) for y in years],
                           [[s["Line"]] + [s[str(y)] for y in years] for s in stmt],
                           currency_cols=list(range(1, len(years) + 1))) + 1
    ws.freeze_panes = "B5"
    ws.auto_filter.ref = None

    ws = wb.create_sheet("All Items")
    title(ws, f"All items - {scenario_name}, {span}",
          "One row per item, every section. Filter by Year, Section or Entity, or build a pivot table.")
    _write_table(ws, 4, audit.COLUMNS, [[r[c] for c in audit.COLUMNS] for r in rows],
                 currency_cols=[audit.COLUMNS.index("Amount")])
    for row in ws.iter_rows(min_row=5, min_col=audit.COLUMNS.index("Amount") + 1,
                            max_col=audit.COLUMNS.index("Amount") + 1):
        row[0].number_format = AUDIT_CURRENCY_FORMAT
    ws.column_dimensions["D"].width = 48
    ws.column_dimensions["G"].width = 32

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Cash flow summary: categories as rows, years as columns
# ---------------------------------------------------------------------------

SUMMARY_FILL = PatternFill("solid", start_color="DDEBF7", end_color="DDEBF7")


def _fill_cashflow_summary(ws: Worksheet, result: SimulationResult, portfolio: Portfolio, years: List[int],
                           start_row: int, heading: str, show_detail: bool = True) -> int:
    """Write one scenario's summary at start_row; returns the next free row."""
    import cashflow_summary
    ws.cell(row=start_row, column=1, value=heading).font = Font(name=FONT_NAME, bold=True, size=12)
    rows = cashflow_summary.build(result, portfolio, years)
    if not show_detail:
        rows = [r for r in rows if r["Kind"] != "detail"]
    hdr = start_row + 1
    for c, h in enumerate(["Line"] + [str(y) for y in years], start=1):
        ws.cell(row=hdr, column=c, value=h)
    _style_header_row(ws, hdr, len(years) + 1)
    r_no = hdr + 1
    section = None
    for row in rows:
        if row["Section"] != section and row["Section"] in ("Income", "Expenses", "Debt service"):
            section = row["Section"]
            ws.cell(row=r_no, column=1, value=section.upper()).font = Font(name=FONT_NAME, bold=True,
                                                                             color="1F4E78")
            r_no += 1
        section = row["Section"]
        kind = row["Kind"]
        label = ("      " + row["Line"]) if kind == "detail" else ("   " + row["Line"]) if kind == "item" else row["Line"]
        ws.cell(row=r_no, column=1, value=label)
        for c, y in enumerate(years, start=2):
            cell = ws.cell(row=r_no, column=c, value=round(row[y], 2))
            cell.number_format = CURRENCY_FORMAT
        bold = kind in ("total", "net")
        italic = kind in ("detail", "memo")
        color = "777777" if kind == "detail" else None
        for c in range(1, len(years) + 2):
            cell = ws.cell(row=r_no, column=c)
            cell.font = Font(name=FONT_NAME, bold=bold, italic=italic, color=color)
            if kind == "net":
                cell.fill = SUMMARY_FILL
        r_no += 1
    ws.column_dimensions["A"].width = 58
    for c in range(2, len(years) + 2):
        ws.column_dimensions[get_column_letter(c)].width = 14
    return r_no + 1


def _write_cashflow_summary_sheet(wb: Workbook, portfolio: Portfolio,
                                  simulation_results: List[SimulationResult]) -> None:
    ws = wb.create_sheet("Cash Flow Summary")
    ws["A1"] = "Cash flow summary"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = ("Income, expenses, debt service and estimated tax by category, a column per year (your share). "
                "Net cash flow = income - expenses - debt service - tax; a surplus is reinvested, a deficit "
                "withdrawn. Grey rows are the items in each category.")
    ws["A2"].font = SUBTITLE_FONT
    row = 4
    names = {s.scenario_id: s.name for s in portfolio.scenarios}
    for res in simulation_results:
        years = [r.year for r in res.annual_results]
        row = _fill_cashflow_summary(ws, res, portfolio, years, row, f"Scenario: {names.get(res.scenario_id, res.scenario_id)}")
    ws.freeze_panes = "B4"


def cashflow_summary_bytes(result: SimulationResult, portfolio: Portfolio, years: List[int],
                           scenario_name: str = "", show_detail: bool = True) -> bytes:
    """The Cash flow summary sub-tab as a one-sheet workbook."""
    import io
    wb = Workbook()
    ws = wb.active
    ws.title = "Cash Flow Summary"
    span = f"{years[0]}" + (f"-{years[-1]}" if len(years) > 1 else "")
    ws["A1"] = f"Cash flow summary - {scenario_name or result.scenario_id}, {span}"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "Net cash flow = income - expenses - debt service - est. tax. Amounts are your share."
    ws["A2"].font = SUBTITLE_FONT
    _fill_cashflow_summary(ws, result, portfolio, years, 4, "", show_detail)
    ws.freeze_panes = "B6"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
