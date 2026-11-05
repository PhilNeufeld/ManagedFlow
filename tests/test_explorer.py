"""Drill-down explorer. The household owns 100% of an LLC and of a trust; the
trust owns 20% of a fund and the LLC 10% of it, so the fund's building
($1,000,000, rent $100,000/yr) shows up on TWO paths: 20% and 10% (= 30% in total)."""
import pytest

import explorer
from models import (
    Asset, Assumptions, Bundle, Cashflow, CashflowDirection, Entity, FlowType, Loan, Ownership, Person,
    Portfolio, Scenario,
)


def plan(view="share"):
    return Portfolio(
        assumptions=Assumptions(start_year=2026, end_year=2027, inflation_rate=0.0, tax_method="flat",
                                default_tax_rates={}),
        people=[Person(person_id="alex", name="Alex", birth_year=1965)],
        entities=[Entity(entity_id="us", name="Us", entity_type="joint"),
                  Entity(entity_id="llc", name="LLC", entity_type="llc"),
                  Entity(entity_id="trust", name="Trust", entity_type="trust"),
                  Entity(entity_id="fund", name="Fund", entity_type="partnership")],
        ownerships=[Ownership(owner_entity_id="us", owned_type="entity", owned_id="llc", percent=1.0),
                    Ownership(owner_entity_id="us", owned_type="entity", owned_id="trust", percent=1.0),
                    Ownership(owner_entity_id="trust", owned_type="entity", owned_id="fund", percent=0.2),
                    Ownership(owner_entity_id="llc", owned_type="entity", owned_id="fund", percent=0.1)],
        assets=[Asset(asset_id="bldg", name="Building", asset_type="real_estate", owner="Fund",
                      initial_value=1_000_000, start_year=2026, entity_id="fund"),
                Asset(asset_id="house", name="House", asset_type="real_estate", owner="LLC",
                      initial_value=300_000, start_year=2026, entity_id="llc")],
        liabilities=[Loan(liability_id="mtg", name="House loan", balance=100_000, annual_interest_rate=0.0,
                          start_year=2026, monthly_payment=1_000, secured_by_asset_id="house")],
        cashflows=[Cashflow(flow_id="rent", name="Building rent", flow_type=FlowType.RENT_INCOME,
                            direction=CashflowDirection.INFLOW, amount=100_000, start_year=2026, asset_id="bldg"),
                   Cashflow(flow_id="salary", name="Salary", flow_type=FlowType.SALARY_INCOME,
                            direction=CashflowDirection.INFLOW, amount=50_000, start_year=2026, person_id="alex"),
                   Cashflow(flow_id="living", name="Living", flow_type=FlowType.LIVING_EXPENSE,
                            direction=CashflowDirection.OUTFLOW, amount=40_000, start_year=2026)],
        bundles=[Bundle(bundle_id="b", name="House bundle", entity_id="llc", asset_ids=["house"])],
        scenarios=[Scenario(scenario_id="base", name="Base", is_base_case=True, ownership_view=view,
                            reinvest_surplus=False)],
    )


def test_tree_follows_ownership_and_collections():
    nodes = explorer.build_tree(plan())
    kinds = {n.label: n.kind for n in nodes.values()}
    assert kinds["Us"] == "Entity" and kinds["House bundle"] == "Collection" and kinds["House loan"] == "Liability"
    fund_paths = sorted(n.factor for n in nodes.values() if n.ref_id == "fund")
    assert fund_paths == pytest.approx([0.1, 0.2])            # one node per ownership path
    house = next(n for n in nodes.values() if n.ref_id == "house")
    assert nodes[house.parent_id].kind == "Collection"         # bundled asset sits in its collection
    assert [nodes[c].label for c in house.children] == ["House loan"]
    alex = next(n for n in nodes.values() if n.label == "Alex")
    assert [nodes[c].label for c in alex.children] == ["Salary"]


@pytest.mark.parametrize("view", ["share", "full"])
def test_each_path_gets_its_portion_and_the_whole_matches_the_simulation(view):
    p = plan(view)
    nodes = explorer.build_tree(p)
    res = explorer.run(p, p.get_scenario("base"))
    share_view = True   # the explorer always works in your share
    bldg = sorted((n for n in nodes.values() if n.ref_id == "bldg"), key=lambda n: n.factor)
    rows = [explorer.node_series(nodes, n.node_id, res, share_view)[0] for n in bldg]
    assert [r["Assets"] for r in rows] == pytest.approx([100_000, 200_000])     # 10% and 20% of $1M
    assert [r["Cash in"] for r in rows] == pytest.approx([10_000, 20_000])
    whole = explorer.node_series(nodes, explorer.ROOT, res, share_view)[0]
    sim = res.annual_results[0]
    assert whole["Net worth"] == pytest.approx(sim.total_net_worth)
    assert whole["Net cash flow"] == pytest.approx(sim.net_cashflow)


def test_liability_node_shows_balance_and_payments():
    p = plan()
    nodes = explorer.build_tree(p)
    loan = next(n for n in nodes.values() if n.ref_id == "mtg")
    row = explorer.node_series(nodes, loan.node_id, explorer.run(p, p.get_scenario("base")))[0]
    assert row["Liabilities"] == pytest.approx(88_000) and row["Principal"] == pytest.approx(12_000)


def test_icicle_children_fill_their_parent():
    p = plan()
    nodes = explorer.build_tree(p)
    res = explorer.run(p, p.get_scenario("base"))
    for measure in ("count", "dollars"):
        rects = explorer.icicle_layout(nodes, explorer.ROOT, res, 2026, measure)
        by_id = {r["node_id"]: r for r in rects}
        for r in rects:
            kids = [by_id[c] for c in nodes[r["node_id"]].children if c in by_id]
            if kids:
                assert kids[0]["y0"] == pytest.approx(r["y0"]) and kids[-1]["y1"] == pytest.approx(r["y1"])


def test_class_colors_are_fixed_per_class():
    assert dict(zip(explorer.CLASSES, explorer.CLASS_COLORS))["Asset"] == "#1baf7a"
    assert len(set(explorer.CLASS_COLORS)) == len(explorer.CLASSES)


# --- period view ---------------------------------------------------------------------------------

def period_plan():
    from models import Event
    p = plan()
    p.assumptions.end_year = 2029
    p.events.append(Event(event_id="bonus", name="Bonus starts", year=2027, month=7))
    p.cashflows.append(Cashflow(flow_id="bonus", name="Bonus", flow_type=FlowType.SALARY_INCOME,
                                direction=CashflowDirection.INFLOW, amount=2_000, frequency="monthly",
                                start_year=2027, start_month=7, start_event_id="bonus", person_id="alex"))
    p.apply_event_dates()
    return p


def test_period_view_rows_groups_flags_and_events():
    p = period_plan()
    nodes = explorer.build_tree(p)
    res = explorer.run(p, p.get_scenario("base"))
    pv = explorer.period_view(p, nodes, explorer.ROOT, res, 2026, 3)
    assert pv["years"] == [2026, 2027, 2028]
    assert pv["events"][2027] == ["Jul: Bonus starts"]
    items = {r["label"]: r for r in pv["rows"] if r["type"] == "item"}
    bonus = items["Bonus"]
    assert [bonus["values"][y] for y in (2026, 2027, 2028)] == pytest.approx([0, 12_000, 24_000])
    assert bonus["flags"] == {2027: "▲", 2028: "▲"}           # +12,000 each year: >= 10% and >= $5,000
    assert "starts Jul 2027 (Bonus starts)" in bonus["note"]
    assert items["Living"]["values"][2026] == -40_000          # outflows negative
    assert items["House loan"]["values"][2026] == pytest.approx(88_000)
    groups = [r["label"] for r in pv["rows"] if r["type"] == "group"]
    assert "House bundle" in groups and "Household: Alex" in groups
    totals = {r["label"]: r for r in pv["rows"] if r["type"] == "total"}
    sim = {r.year: r for r in res.annual_results}
    assert totals["Net worth"]["values"][2027] == pytest.approx(sim[2027].total_net_worth)
    assert totals["Net cash flow"]["values"][2027] == pytest.approx(sim[2027].net_cashflow)


def test_period_view_only_changes_and_scope():
    p = period_plan()
    nodes = explorer.build_tree(p)
    res = explorer.run(p, p.get_scenario("base"))
    only = explorer.period_view(p, nodes, explorer.ROOT, res, 2026, 3, only_changes=True)
    labels = [r["label"] for r in only["rows"] if r["type"] == "item"]
    assert "Bonus" in labels and "Salary" not in labels       # flat salary has no flag and no event
    llc = next(n.node_id for n in nodes.values() if n.ref_id == "llc")
    scoped = explorer.period_view(p, nodes, llc, res, 2026, 2)
    assert {r["label"] for r in scoped["rows"] if r["type"] == "item"} >= {"House", "House loan"}
    assert "Salary" not in {r["label"] for r in scoped["rows"]}


def test_period_view_is_capped_at_five_years_and_the_plan_end():
    p = period_plan()
    nodes = explorer.build_tree(p)
    res = explorer.run(p, p.get_scenario("base"))
    assert explorer.period_view(p, nodes, explorer.ROOT, res, 2026, 9)["years"] == [2026, 2027, 2028, 2029]


def test_export_has_period_view_sheet(tmp_path):
    from openpyxl import load_workbook
    import exports, simulation
    p = period_plan()
    wb = load_workbook(exports.export_to_excel(p, simulation.run_all_scenarios(p), tmp_path / "x.xlsx"))
    ws = wb["Period View"]
    assert ws["A4"].value == "Item" and ws["A5"].value == "Events"
    assert any(ws.cell(row=r, column=1).value == "Household: Alex" for r in range(1, ws.max_row + 1))


def test_explore_totals_include_unplanned_actuals():
    from models import Actual
    p = plan()
    p.actuals.append(Actual(actual_id=1, year=2026, amount=2_500, name="Roof patch", category="repairs",
                            direction="outflow", asset_id="house"))
    p.actuals.append(Actual(actual_id=2, year=2026, amount=1_000, name="Refund", category="other"))
    res = explorer.run(p, p.get_scenario("base"))
    nodes = explorer.build_tree(res.working_portfolio)
    labels = {n.label for n in nodes.values()}
    assert {"Unplanned: Roof patch", "Unplanned: Refund"} <= labels
    whole = explorer.node_series(nodes, explorer.ROOT, res)[0]
    assert whole["Net cash flow"] == pytest.approx(res.annual_results[0].net_cashflow)
