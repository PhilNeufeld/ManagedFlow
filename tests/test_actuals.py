"""Actuals and forecasts: import, matching, simulation, plan-vs-actual and
trend breaks. Illustrative numbers you can check by hand:

  Orchard crop sales: plan $130,000 in 2026, growing 2%/yr
  12 Elm St rent:        plan $1,000/month ($12,000/yr), no growth
  Plaza distribution:      plan $200,000/yr at 100%; you own 7.45%
"""
import pytest

import actuals
import simulation
from migrations import migrate
from models import (
    Actual, Asset, Assumptions, Cashflow, CashflowDirection, Entity, FlowType, Frequency, Ownership, Portfolio,
    Scenario, ValidationError,
)

# Rows link by cashflow_id (the planned cashflow's key); the last row has none: not in the plan.
CSV = """year,kind,cashflow_id,name,category,direction,amount,month,asset_id,notes
2026,actual,crop,Orchard crop sales,crop_sales,inflow,"$118,500",,,Lower yield
2027,forecast,crop,Orchard crop sales,,,150000,,,
2026,actual,elm_rent,Elm rent,,,1100,1,,
2026,actual,elm_rent,Elm rent,,,1100,2,,
2026,actual,elm_rent,Elm rent,,,1100,3,,
2026,actual,,Well pump repair,repairs,outflow,4200,,orchard,Not budgeted
"""


def household():
    return Portfolio(
        assumptions=Assumptions(start_year=2026, end_year=2028, inflation_rate=0.0),
        entities=[Entity(entity_id="oakhill", name="Oak Hill Investments", entity_type="llc"),
                  Entity(entity_id="lp", name="Rivera Family LP", entity_type="partnership"),
                  Entity(entity_id="plaza", name="Harbor Plaza", entity_type="llc")],
        ownerships=[Ownership(owner_entity_id="lp", owned_type="entity", owned_id="plaza", percent=0.0745)],
        assets=[Asset(asset_id="orchard", name="Orchard", asset_type="business", owner="Oak Hill",
                      initial_value=1_000_000, start_year=2026, entity_id="oakhill"),
                Asset(asset_id="elm", name="12 Elm St", asset_type="real_estate", owner="Oak Hill",
                      initial_value=300_000, start_year=2026, entity_id="oakhill"),
                Asset(asset_id="plaza_center", name="Plaza center", asset_type="real_estate", owner="Plaza",
                      initial_value=3_000_000, start_year=2026, entity_id="plaza")],
        cashflows=[
            Cashflow(flow_id="crop", name="Orchard crop sales", flow_type=FlowType.CROP_SALES,
                     direction=CashflowDirection.INFLOW, amount=130_000, start_year=2026,
                     annual_growth_rate=0.02, asset_id="orchard"),
            Cashflow(flow_id="elm_rent", name="Elm rent", flow_type=FlowType.RENT_INCOME,
                     direction=CashflowDirection.INFLOW, amount=1_000, frequency=Frequency.MONTHLY,
                     start_year=2026, asset_id="elm"),
            Cashflow(flow_id="plaza_dist", name="Plaza distribution", flow_type=FlowType.BUSINESS_DISTRIBUTION,
                     direction=CashflowDirection.INFLOW, amount=200_000, start_year=2026, asset_id="plaza_center"),
        ],
        scenarios=[Scenario(scenario_id="base", name="Base"),
                   Scenario(scenario_id="plan_only", name="Plan only", use_actuals=False)],
    )


def imported(p=None):
    p = p or household()
    rows = actuals.match_rows(actuals.parse_csv(CSV), p)
    actuals.commit_rows(rows, p)
    return p, rows


def income(p, scenario_id, year):
    results = simulation.run_simulation(p, p.get_scenario(scenario_id)).annual_results
    return next(r for r in results if r.year == year).total_gross_income


# --- reading the CSV ------------------------------------------------------------------------

def test_csv_is_read_and_cleaned():
    rows = actuals.parse_csv(CSV)
    assert len(rows) == 6 and all(r.ok for r in rows)
    assert rows[0].amount == 118_500            # "$118,500" -> 118500
    assert rows[0].category == "crop_sales"
    assert rows[2].month == 1 and rows[0].month is None


def test_bad_rows_are_reported_not_fatal():
    bad = "year,kind,name,category,direction,amount,month\n" \
          "20x6,actual,X,rent,inflow,10,,\n2026,maybe,Y,rent,inflow,10,13\n2026,actual,Z,rent,sideways,-5,\n"
    rows = actuals.parse_csv(bad)
    assert [len(r.errors) for r in rows] == [1, 2, 2]


def test_missing_columns_are_named():
    with pytest.raises(ValueError, match="amount"):
        actuals.parse_csv("year,kind,name,category,direction\n2026,actual,X,rent,inflow\n")


# --- matching to the plan -------------------------------------------------------------------------

def test_rows_link_by_key():
    _, rows = imported()
    assert [r.flow_id for r in rows] == ["crop", "crop", "elm_rent", "elm_rent", "elm_rent", None]
    assert rows[2].asset_id == "elm"                      # the asset comes from the linked cashflow
    assert (rows[1].category, rows[1].direction) == ("crop_sales", "inflow")   # filled from the cashflow
    assert rows[5].asset_id == "orchard" and "not in the plan" in rows[5].match_note


def test_a_matching_name_alone_never_links():
    csv_text = ("year,kind,cashflow_id,name,category,direction,amount\n"
                "2026,actual,,Orchard crop sales,crop_sales,inflow,100\n")
    rows = actuals.link_rows(actuals.parse_csv(csv_text), household())
    assert rows[0].flow_id is None and rows[0].ok


def test_bad_keys_are_reported():
    csv_text = ("year,kind,cashflow_id,name,category,direction,amount,asset_id\n"
                "2026,actual,nope,X,,,100,\n"                       # no such cashflow
                "2026,actual,crop,X,,outflow,100,\n"                 # crop sales are an inflow
                "2026,actual,,Y,,,100,\n"                            # unplanned without category/direction
                "2026,actual,,Z,repairs,outflow,100,barn\n")         # no such asset
    rows = actuals.link_rows(actuals.parse_csv(csv_text), household())
    assert "no planned cashflow #nope" in rows[0].errors[0]
    assert "disagrees" in rows[1].errors[0]
    assert len(rows[2].errors) == 2
    assert "no asset #barn" in rows[3].errors[0]


def test_template_lists_every_planned_cashflow_with_its_key_and_blank_rows_are_skipped():
    p = household()
    text = actuals.template_for(p, 2026)
    rows = actuals.parse_csv(text)
    assert rows == []                                     # nothing filled in yet: all skipped
    filled = text.replace(",inflow,,", ",inflow,500,", 1)  # fill the first row's amount
    rows = actuals.link_rows(actuals.parse_csv(filled), p)
    assert len(rows) == 1 and rows[0].flow_id == p.cashflows[0].flow_id and rows[0].amount == 500
    assert {line.split(",")[2] for line in text.splitlines()[1:]} == {str(cf.flow_id) for cf in p.cashflows}


def test_reimporting_replaces_instead_of_duplicating():
    p, _ = imported()
    count = len(p.actuals)
    imported(p)
    assert len(p.actuals) == count


# --- what the simulation uses -------------------------------------------------------------------------

def test_full_year_actual_replaces_the_plan():
    p, _ = imported()
    # plan 130,000 crop + 12,000 rent + 7.45% x 200,000 (14,900) = 156,900
    assert income(p, "plan_only", 2026) == pytest.approx(156_900)
    # actual: crop 118,500; rent Jan-Mar actual 3,300 + Apr-Dec plan 9,000; Plaza plan 14,900
    assert income(p, "base", 2026) == pytest.approx(118_500 + 12_300 + 14_900)


def test_part_year_of_monthly_actuals_is_topped_up_with_plan():
    p, _ = imported()
    amount, source = p.actual_annual_amount(p.get_cashflow("elm_rent"), 2026)
    assert amount == pytest.approx(3 * 1_100 + 9 * 1_000) and source == "actual + plan"


def test_forecast_is_used_for_its_year():
    p, _ = imported()
    assert income(p, "base", 2027) == pytest.approx(150_000 + 12_000 + 14_900)
    assert income(p, "base", 2028) == pytest.approx(130_000 * 1.02 ** 2 + 12_000 + 14_900)  # back to plan


def test_actual_beats_forecast_for_the_same_month():
    p = household()
    for kind, amount in [("forecast", 900), ("actual", 1_250)]:
        p.add_actual(Actual(actual_id=f"a_{kind}", year=2026, month=5, kind=kind, amount=amount,
                            name="Elm rent", flow_id="elm_rent"))
    amount, _ = p.actual_annual_amount(p.get_cashflow("elm_rent"), 2026)
    assert amount == pytest.approx(1_250 + 11 * 1_000)


def test_actuals_are_your_share_in_both_views():
    p = household()
    p.add_actual(Actual(actual_id="d", year=2026, amount=15_000, name="Plaza distribution", flow_id="plaza_dist"))
    p.scenarios.append(Scenario(scenario_id="full", name="100%", ownership_view="full"))
    share = income(p, "base", 2026) - (130_000 + 12_000)
    full = income(p, "full", 2026) - (130_000 + 12_000)
    assert share == pytest.approx(15_000)             # what you actually received
    assert full == pytest.approx(15_000 / 0.0745)     # the whole distribution it implies


def test_stored_plan_is_untouched_and_overrides_are_never_saved():
    p, _ = imported()
    simulation.run_simulation(p, p.get_scenario("base"))
    assert p.get_cashflow("crop").year_overrides == {}
    assert "year_overrides" not in p.to_dict()["cashflows"][0]


# --- reports -----------------------------------------------------------------------------------------

def test_plan_vs_actual_flags_big_misses_and_unplanned_items():
    p, _ = imported()
    rows = {r["Item"]: r for r in actuals.plan_vs_actual(p, 2026)}
    crop = rows["Orchard crop sales"]
    assert (crop["Planned"], crop["Actual / forecast"], crop["Difference"]) == pytest.approx((130_000, 118_500, -11_500))
    # 11,500 / 130,000 = 8.8% short: under the default 10% threshold, so not flagged...
    assert not crop["Flag"]
    # ...but flagged when you tighten the threshold to 5%
    tight = {r["Item"]: r for r in actuals.plan_vs_actual(p, 2026, pct=0.05)}
    assert tight["Orchard crop sales"]["Flag"]
    rent = rows["Elm rent"]
    assert rent["Difference"] == pytest.approx(300) and not rent["Flag"]  # under $1,000
    assert rows["Well pump repair"]["Source"] == "actual, NOT IN PLAN"


def test_trend_break_on_next_years_forecast():
    p, _ = imported()
    row = next(r for r in actuals.trend_breaks(p, 2027) if r["Item"] == "Orchard crop sales")
    # 2026 actual 118,500 grown 2% = 120,870 expected; forecast 150,000 is +24%
    assert row["Prior year is"] == "actual" and row["This year is"] == "forecast"
    assert row["Expected"] == pytest.approx(120_870)
    assert row["Gap"] == pytest.approx(29_130) and row["Flag"]


def test_forecast_that_continues_the_trend_is_not_flagged():
    p, _ = imported()
    p.add_actual(Actual(actual_id="f", year=2027, kind="forecast", amount=121_500,
                        name="Orchard crop sales", flow_id="crop"))
    row = next(r for r in actuals.trend_breaks(p, 2027) if r["Item"] == "Orchard crop sales")
    assert not row["Flag"]


# --- integrity ---------------------------------------------------------------------------------------------

def test_deleting_a_cashflow_keeps_its_actuals_as_unplanned():
    p, _ = imported()
    p.remove_cashflow("crop")
    assert all(a.flow_id is None for a in p.actuals if "crop" in a.name.lower())


def test_actual_must_point_at_a_real_cashflow():
    with pytest.raises(ValidationError):
        household().add_actual(Actual(actual_id="x", year=2026, amount=1, flow_id="nope"))


def test_actuals_survive_save_and_load():
    p, _ = imported()
    assert Portfolio.from_dict(p.to_dict()).to_dict() == p.to_dict()


def test_old_files_get_no_actuals(v0_1_data):
    new = migrate(v0_1_data)
    assert new["actuals"] == [] and all(s["use_actuals"] for s in new["scenarios"])


# --- status and class -------------------------------------------------------------------------

STATUS_CSV = """year,kind,name,category,direction,amount,month,entity,asset,notes,status,class
2026,actual,Orchard crop sales,crop_sales,inflow,10000,,Oak Hill Investments,Orchard,,confirmed,Crop 2026
2026,actual,Crop insurance,crop_sales,inflow,58000,,Oak Hill Investments,Orchard,,Confirmed,Crop 2026
2026,actual,2025 crop bonus,crop_sales,inflow,48619,,Oak Hill Investments,Orchard,,estimated,Crop 2025
"""


def test_status_and_class_are_read_and_kept():
    p = household()
    rows = actuals.match_rows(actuals.parse_csv(STATUS_CSV), p)
    for r in rows:
        r.flow_id = "crop"
    assert actuals.commit_rows(rows, p) == 3
    by_name = {a.name: a for a in p.actuals}
    assert by_name["Crop insurance"].status == "confirmed"       # case-insensitive
    assert by_name["2025 crop bonus"].record_class == "Crop 2025"
    # three receipts for one cashflow in one year sit side by side and add up
    assert p.actual_annual_amount(p.get_cashflow("crop"), 2026)[0] == 10_000 + 58_000 + 48_619


def test_status_is_optional_and_checked():
    rows = actuals.parse_csv(CSV)
    assert all(r.status == "estimated" and r.record_class == "" for r in rows)
    bad = actuals.parse_csv(STATUS_CSV.replace("confirmed,Crop 2026\n2026", "maybe,Crop 2026\n2026", 1))
    assert "status" in bad[0].errors[0]
    with pytest.raises(ValidationError):
        Actual(actual_id="x", year=2026, amount=1, status="guess")


def test_status_and_class_survive_save_and_load():
    a = Actual(actual_id="x", year=2026, amount=5, status="provisional", record_class="Crop 2026")
    back = Actual.from_dict(a.to_dict())
    assert (back.status, back.record_class) == ("provisional", "Crop 2026")


# --- unplanned actuals: count once, in their own year ---------------------------------------------------

def year_result(p, scenario_id, year):
    return next(r for r in simulation.run_simulation(p, p.get_scenario(scenario_id)).annual_results if r.year == year)


def test_an_unplanned_repair_counts_once_in_its_year():
    p, _ = imported()                       # includes "Well pump repair" $4,200, not in the plan
    p.scenarios.append(Scenario(scenario_id="ignore", name="Ignore unplanned", count_unplanned_actuals=False))
    with_it, without = year_result(p, "base", 2026), year_result(p, "ignore", 2026)
    assert with_it.total_expenses - without.total_expenses == pytest.approx(4_200)
    assert with_it.total_gross_income == pytest.approx(without.total_gross_income)
    later_with, later_without = year_result(p, "base", 2027), year_result(p, "ignore", 2027)
    assert later_with.total_expenses == pytest.approx(later_without.total_expenses)   # never repeats


def test_an_unplanned_receipt_on_a_shared_asset_is_your_share():
    p = household()
    p.add_actual(Actual(actual_id="x", year=2026, amount=1_000, name="Settlement", category="other",
                        direction="inflow", asset_id="plaza_center"))   # you own 7.45%; you received $1,000
    base = year_result(p, "base", 2026).total_gross_income
    plan = year_result(p, "plan_only", 2026).total_gross_income
    assert base - plan == pytest.approx(1_000)


def test_unplanned_household_item_and_actual_beats_forecast():
    p = household()
    p.add_actual(Actual(actual_id="f", year=2026, amount=9_000, name="Gift", kind="forecast", category="other"))
    p.add_actual(Actual(actual_id="a", year=2026, amount=5_000, name="Gift", kind="actual", category="other"))
    assert year_result(p, "base", 2026).total_gross_income - year_result(p, "plan_only", 2026).total_gross_income \
        == pytest.approx(5_000)


def test_unplanned_items_dont_change_the_stored_plan():
    p, _ = imported()
    before = len(p.cashflows)
    simulation.run_simulation(p, p.get_scenario("base"))
    assert len(p.cashflows) == before and not any(str(cf.flow_id).startswith("unplanned") for cf in p.cashflows)


# --- re-linking a stored record -------------------------------------------------------------------------------

def test_relinking_a_record_moves_it_into_the_plan():
    p, _ = imported()
    rec = next(a for a in p.actuals if a.name == "Well pump repair")
    assert rec in p.unplanned_actuals(2026)
    p.add_cashflow(Cashflow(flow_id="pist_repairs", name="Orchard repairs", flow_type=FlowType.REPAIRS,
                            direction=CashflowDirection.OUTFLOW, amount=3_000, start_year=2026,
                            asset_id="orchard"))
    p.relink_actual(rec.actual_id, "pist_repairs")
    assert rec.flow_id == "pist_repairs" and rec.asset_id == "orchard" and not p.unplanned_actuals()
    amount, source = p.actual_annual_amount(p.get_cashflow("pist_repairs"), 2026)
    assert amount == 4_200 and source == "actual"                 # replaces the $3,000 plan
    p.relink_actual(rec.actual_id, None)
    assert rec.flow_id is None


def test_relinking_to_an_unknown_cashflow_is_refused():
    p, _ = imported()
    with pytest.raises(ValidationError):
        p.relink_actual(p.actuals[0].actual_id, "nope")


def test_imported_records_get_integer_keys():
    p, _ = imported()
    assert [a.actual_id for a in p.actuals] == list(range(1, len(p.actuals) + 1))
