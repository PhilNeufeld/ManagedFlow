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
    _write_assets_sheet(wb, portfolio)
    _write_cashflows_sheet(wb, portfolio)
    _write_bundles_sheet(wb, portfolio)
    _write_scenario_settings_sheet(wb, portfolio)
    _write_annual_results_sheet(wb, simulation_results)
    _write_asset_detail_sheet(wb, simulation_results)
    _write_cashflow_detail_sheet(wb, portfolio, simulation_results)
    _write_tax_estimate_sheet(wb, simulation_results)
    _write_drawdowns_sheet(wb, simulation_results)
    _write_charts_sheet(wb, simulation_results)
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
        ["Number of household-level cashflows", len(portfolio.household_cashflows)],
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
    flow_labels = ", ".join(flow_name_by_id.get(fid, fid) for fid in bundle.standalone_cashflow_ids)
    return [bundle.bundle_id, bundle.name, entity_label, asset_labels, flow_labels, bundle.notes]


def _write_bundles_sheet(wb: Workbook, portfolio: Portfolio) -> None:
    ws = wb.create_sheet("Bundles")
    ws["A1"] = "Bundles (Assets + Cashflows grouped under an Entity)"
    ws["A1"].font = TITLE_FONT
    entity_name_by_id = {e.entity_id: e.name for e in portfolio.entities}
    asset_name_by_id = {a.asset_id: a.name for a in portfolio.assets}
    flow_name_by_id = {cf.flow_id: cf.name for cf in portfolio.household_cashflows}
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
    "Active", "Do Not Sell", "Sale Year", "Notes",
]


def _asset_row(asset, entity_name_by_id: Dict[str, str]) -> list:
    entity_label = entity_name_by_id.get(asset.entity_id, asset.entity_id or "")
    return [
        asset.asset_id, asset.name, asset.asset_type.value, asset.owner, entity_label,
        asset.initial_value, asset.current_value, asset.start_year, asset.annual_growth_rate,
        asset.liquidity_category.value, asset.tax_category.value, asset.active,
        asset.do_not_sell, asset.sale_year, asset.notes,
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
    "Total Net Worth", "Liquid Net Worth", "Illiquid Net Worth",
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
            ])
    _write_table(ws, 3, ANNUAL_RESULTS_HEADERS, rows, currency_cols=list(range(2, 12)))

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
