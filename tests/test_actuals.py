"""Actuals and forecasts: import, matching, simulation, plan-vs-actual and
trend breaks. Illustrative numbers you can check by hand:

  Pistachios crop sales: plan $130,000 in 2026, growing 2%/yr
  4392 Brix rent:        plan $1,000/month ($12,000/yr), no growth
  LVM distribution:      plan $200,000/yr at 100%; you own 7.45%
"""
import pytest

import actuals
import simulation
from migrations import migrate
from models import (
    Actual, Asset, Assumptions, Cashflow, CashflowDirection, Entity, FlowType, Frequency, Ownership, Portfolio,
    Scenario, ValidationError,
)

CSV = """year,kind,name,category,direction,amount,month,entity,asset,notes
2026,actual,Pistachio crop sales,crop_sales,inflow,"$118,500",,Branfeld Investments,Pistachios,Lower yield
2027,forecast,Pistachio crop sales,crop_sales,inflow,150000,,Branfeld Investments,Pistachios,
2026,actual,Brix rent,rent,inflow,1100,1,Branfeld Investments,4392 brix,
2026,actual,Brix rent,rent,inflow,1100,2,Branfeld Investments,4392 Brix,
2026,actual,Brix rent,rent,inflow,1100,3,Branfeld Investments,4392 Brix,
2026,actual,Well pump repair,repairs,outflow,4200,,Branfeld Investments,Pistachios,Not budgeted
"""


def household():
    return Portfolio(
        assumptions=Assumptions(start_year=2026, end_year=2028, inflation_rate=0.0),
        entities=[Entity(entity_id="branfeld", name="Branfeld Investments", entity_type="llc"),
                  Entity(entity_id="lp", name="Neufeld Family LP", entity_type="partnership"),
                  Entity(entity_id="lvm", name="Loma Vista Marketplace", entity_type="llc")],
        ownerships=[Ownership(owner_entity_id="lp", owned_type="entity", owned_id="lvm", percent=0.0745)],
        assets=[Asset(asset_id="pistachios", name="Pistachios", asset_type="business", owner="Branfeld",
                      initial_value=1_000_000, start_year=2026, entity_id="branfeld"),
                Asset(asset_id="brix", name="4392 Brix", asset_type="real_estate", owner="Branfeld",
                      initial_value=300_000, start_year=2026, entity_id="branfeld"),
                Asset(asset_id="lvm_center", name="LVM center", asset_type="real_estate", owner="LVM",
                      initial_value=3_000_000, start_year=2026, entity_id="lvm")],
        cashflows=[
            Cashflow(flow_id="crop", name="Pistachio crop sales", flow_type=FlowType.CROP_SALES,
                     direction=CashflowDirection.INFLOW, amount=130_000, start_year=2026,
                     annual_growth_rate=0.02, asset_id="pistachios"),
            Cashflow(flow_id="brix_rent", name="Brix rent", flow_type=FlowType.RENT_INCOME,
                     direction=CashflowDirection.INFLOW, amount=1_000, frequency=Frequency.MONTHLY,
                     start_year=2026, asset_id="brix"),
            Cashflow(flow_id="lvm_dist", name="LVM distribution", flow_type=FlowType.BUSINESS_DISTRIBUTION,
                     direction=CashflowDirection.INFLOW, amount=200_000, start_year=2026, asset_id="lvm_center"),
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

def test_rows_match_their_planned_cashflows():
    _, rows = imported()
    assert [r.flow_id for r in rows] == ["crop", "crop", "brix_rent", "brix_rent", "brix_rent", None]
    assert rows[2].asset_id == "brix"  # "4392 brix" matched regardless of case
    assert "not in the plan" in rows[5].match_note


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
    # actual: crop 118,500; rent Jan-Mar actual 3,300 + Apr-Dec plan 9,000; LVM plan 14,900
    assert income(p, "base", 2026) == pytest.approx(118_500 + 12_300 + 14_900)


def test_part_year_of_monthly_actuals_is_topped_up_with_plan():
    p, _ = imported()
    amount, source = p.actual_annual_amount(p.get_cashflow("brix_rent"), 2026)
    assert amount == pytest.approx(3 * 1_100 + 9 * 1_000) and source == "actual + plan"


def test_forecast_is_used_for_its_year():
    p, _ = imported()
    assert income(p, "base", 2027) == pytest.approx(150_000 + 12_000 + 14_900)
    assert income(p, "base", 2028) == pytest.approx(130_000 * 1.02 ** 2 + 12_000 + 14_900)  # back to plan


def test_actual_beats_forecast_for_the_same_month():
    p = household()
    for kind, amount in [("forecast", 900), ("actual", 1_250)]:
        p.add_actual(Actual(actual_id=f"a_{kind}", year=2026, month=5, kind=kind, amount=amount,
                            name="Brix rent", flow_id="brix_rent"))
    amount, _ = p.actual_annual_amount(p.get_cashflow("brix_rent"), 2026)
    assert amount == pytest.approx(1_250 + 11 * 1_000)


def test_actuals_are_your_share_in_both_views():
    p = household()
    p.add_actual(Actual(actual_id="d", year=2026, amount=15_000, name="LVM distribution", flow_id="lvm_dist"))
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
    crop = rows["Pistachio crop sales"]
    assert (crop["Planned"], crop["Actual / forecast"], crop["Difference"]) == pytest.approx((130_000, 118_500, -11_500))
    # 11,500 / 130,000 = 8.8% short: under the default 10% threshold, so not flagged...
    assert not crop["Flag"]
    # ...but flagged when you tighten the threshold to 5%
    tight = {r["Item"]: r for r in actuals.plan_vs_actual(p, 2026, pct=0.05)}
    assert tight["Pistachio crop sales"]["Flag"]
    rent = rows["Brix rent"]
    assert rent["Difference"] == pytest.approx(300) and not rent["Flag"]  # under $1,000
    assert rows["Well pump repair"]["Source"] == "actual, NOT IN PLAN"


def test_trend_break_on_next_years_forecast():
    p, _ = imported()
    row = next(r for r in actuals.trend_breaks(p, 2027) if r["Item"] == "Pistachio crop sales")
    # 2026 actual 118,500 grown 2% = 120,870 expected; forecast 150,000 is +24%
    assert row["Prior year is"] == "actual" and row["This year is"] == "forecast"
    assert row["Expected"] == pytest.approx(120_870)
    assert row["Gap"] == pytest.approx(29_130) and row["Flag"]


def test_forecast_that_continues_the_trend_is_not_flagged():
    p, _ = imported()
    p.add_actual(Actual(actual_id="f", year=2027, kind="forecast", amount=121_500,
                        name="Pistachio crop sales", flow_id="crop"))
    row = next(r for r in actuals.trend_breaks(p, 2027) if r["Item"] == "Pistachio crop sales")
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
2026,actual,Pistachio crop sales,crop_sales,inflow,10000,,Branfeld Investments,Pistachios,,confirmed,Crop 2026
2026,actual,Crop insurance,crop_sales,inflow,58000,,Branfeld Investments,Pistachios,,Confirmed,Crop 2026
2026,actual,2025 crop bonus,crop_sales,inflow,48619,,Branfeld Investments,Pistachios,,estimated,Crop 2025
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
