"""Cash flow summary: categories add back to each year's totals, for the fictional example
household, and the Excel versions build."""
import io

import pytest
from openpyxl import load_workbook

import cashflow_summary
import example_data
import exports
import simulation


@pytest.fixture(scope="module")
def example():
    p = example_data.build_example_portfolio()
    return p, simulation.run_all_scenarios(p)


def test_categories_add_up_every_year_every_scenario(example):
    p, results = example
    for res in results:
        years = [r.year for r in res.annual_results]
        rows = cashflow_summary.build(res, p, years)
        bad = [c for c in cashflow_summary.check(rows, res, years) if not c["OK"]]
        assert bad == [], (res.scenario_id, bad[:3])


def test_details_add_up_to_their_category(example):
    p, results = example
    res = results[0]
    years = [r.year for r in res.annual_results][:5]
    rows = cashflow_summary.build(res, p, years)
    for item in [r for r in rows if r["Kind"] == "item"]:
        parts = [r for r in rows if r["Kind"] == "detail" and r["Section"] == item["Section"]
                 and r["Category"] == item["Line"]]
        assert parts, item["Line"]
        for y in years:
            assert sum(r[y] for r in parts) == pytest.approx(item[y], abs=0.01)


def test_net_matches_the_results(example):
    p, results = example
    res = results[0]
    years = [r.year for r in res.annual_results]
    rows = cashflow_summary.build(res, p, years)
    net = next(r for r in rows if r["Kind"] == "net")
    for r in res.annual_results:
        assert net[r.year] == pytest.approx(r.surplus_or_deficit, abs=0.01)


def test_year_range_is_respected(example):
    p, results = example
    res = results[0]
    years = [r.year for r in res.annual_results][1:3]
    rows = cashflow_summary.build(res, p, years)
    assert all(set(k for k in r if isinstance(k, int)) == set(years) for r in rows)


def test_loans_are_grouped_by_kind():
    from types import SimpleNamespace as NS
    rentals, types = {"r1"}, {"b1": "business"}
    loan = lambda kind, secured=None: NS(liability_type=NS(value=kind), secured_by_asset_id=secured)
    cat = cashflow_summary._debt_category
    assert cat(loan("mortgage", "r1"), rentals, types) == "Rental property loans"
    assert cat(loan("mortgage", "home"), rentals, types) == "Home mortgage"
    assert cat(loan("heloc", "home"), rentals, types) == "HELOC & credit lines"
    assert cat(loan("car"), rentals, types) == "Car loans"
    assert cat(loan("student"), rentals, types) == "Student loans"
    assert cat(loan("other", "b1"), rentals, types) == "Business & farm loans"


def test_excel_downloads_build(example):
    p, results = example
    res = results[0]
    years = [r.year for r in res.annual_results][:5]
    wb = load_workbook(io.BytesIO(exports.cashflow_summary_bytes(res, p, years, "Base", True)))
    ws = wb["Cash Flow Summary"]
    labels = [ws.cell(row=i, column=1).value for i in range(1, ws.max_row + 1)]
    assert any(l and l.startswith("Net cash flow") for l in labels)
    assert any(l == "INCOME" for l in labels)


def test_full_export_has_the_summary_sheet(example, tmp_path):
    p, results = example
    path = exports.export_to_excel(p, results, tmp_path / "out.xlsx")
    assert "Cash Flow Summary" in load_workbook(path).sheetnames
