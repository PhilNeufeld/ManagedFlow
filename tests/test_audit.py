"""Audit: every annual total broken into its items, and the items add back up --
for every year and scenario of the fictional example household."""
import pytest

import audit
import example_data
import exports
import simulation
from models import AnnualHouseholdResult


@pytest.fixture(scope="module")
def example():
    p = example_data.build_example_portfolio()
    return p, simulation.run_all_scenarios(p)


def test_every_check_passes_every_year_every_scenario(example):
    p, results = example
    for res in results:
        failed = [c for c in audit.checks(res) + audit.asset_total_check(res, p) if not c["OK"]]
        assert failed == [], (res.scenario_id, failed[:3])


def test_items_add_up_to_the_summary(example):
    p, results = example
    res = results[0]
    y = res.annual_results[0]
    rows = audit.audit_rows(res, p, years=[y.year])
    total = lambda section: sum(r["Amount"] for r in rows if r["Section"] == section)
    assert total("Gross income") == pytest.approx(y.total_gross_income, abs=0.05)
    assert total("Expenses") == pytest.approx(y.total_expenses, abs=0.05)
    ends = sum(r["Amount"] for r in rows if r["Section"] == "Assets" and r["Detail"] == "Ending value")
    assert ends == pytest.approx(y.total_assets, abs=1.0)


def test_every_item_has_a_source(example):
    p, results = example
    rows = audit.audit_rows(results[0], p)
    for r in rows:
        if r["Section"] in ("Gross income", "Expenses", "Debt service", "Assets"):
            assert r["Source ID"], r


def test_expenses_include_each_loans_payments(example):
    p, results = example
    res = results[0]
    y = next(r for r in res.annual_results if r.total_debt_service)
    rows = audit.audit_rows(res, p, years=[y.year])
    loan_rows = [r for r in rows if r["Section"] == "Expenses" and r["Detail"] == "debt service"]
    assert sum(r["Amount"] for r in loan_rows) == pytest.approx(y.total_debt_service, abs=0.05)


def test_a_broken_total_is_flagged(example):
    p, results = example
    res = results[0]
    y = res.annual_results[0]
    y.total_gross_income += 100            # tamper with a copy's total
    try:
        bad = [c for c in audit.checks(res, [y.year]) if not c["OK"]]
        assert [c["Check"] for c in bad][:1] == ["Gross income"] and bad[0]["Difference"] == pytest.approx(100)
    finally:
        y.total_gross_income -= 100


def test_pivot_has_a_column_per_year_and_a_total(example):
    p, results = example
    res = results[0]
    years = [r.year for r in res.annual_results][:3]
    df = audit.pivot(audit.audit_rows(res, p, years=years), "Gross income", years)
    assert [c for c in df.columns if c.isdigit()] == [str(y) for y in years] and "Total" in df.columns


def test_line_items_survive_saving_results(example):
    p, results = example
    y = results[0].annual_results[0]
    again = AnnualHouseholdResult.from_dict(y.to_dict())
    assert again.line_items == y.line_items and again.reinvestment_breakdown == y.reinvestment_breakdown


def test_excel_has_audit_sheets(example, tmp_path):
    from openpyxl import load_workbook
    p, results = example
    path = exports.export_to_excel(p, results, tmp_path / "out.xlsx")
    wb = load_workbook(path, read_only=True)
    assert "Audit" in wb.sheetnames and "Audit Checks" in wb.sheetnames
    checks = list(wb["Audit Checks"].iter_rows(min_row=5, values_only=True))
    assert checks and all(row[-1] == "OK" for row in checks)
