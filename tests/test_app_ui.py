"""Screen tests: run app.py headlessly (Streamlit's AppTest), click buttons and
fill in fields like a user would, then check what the screen shows AND what
was stored. Each test starts from a portfolio held in memory, so nothing is
written to your data folder.

These are slower than the other tests (a few seconds each). Skip them with:
    python -m pytest -m "not ui"
"""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

import sys  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from models import Entity, Membership, Person, Portfolio  # noqa: E402

pytestmark = pytest.mark.ui
APP = str(Path(__file__).resolve().parent.parent / "app.py")


# --- helpers -----------------------------------------------------------------------------------

def start(portfolio, tmp_path):
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["portfolio"] = portfolio
    at.session_state["portfolio_path"] = tmp_path / "not_saved.json"
    return at.run()


def editor_widget(at, collection, kind, field):
    """Editor widgets have keys like _editor_<kind>_<field>_<n>."""
    return next(w for w in collection if w.key and w.key.startswith(f"_editor_{kind}_{field}_"))


def click(at, **match):
    (attr, value), = match.items()
    return next(b for b in at.button if getattr(b, attr) == value).click().run()


def table(at, column):
    """The first table on screen that has `column`."""
    return next(d.value for d in at.dataframe if column in d.value.columns)


def stored(at) -> Portfolio:
    return at.session_state["portfolio"]


def no_crash(at):
    assert not at.exception, [e.value for e in at.exception]


def couple():
    """Phil (personal entity with birthday) and Debbie (person only), plus a joint entity."""
    return Portfolio(
        people=[Person(person_id="person_phil", name="Phil", birth_year=1965, birth_month=2),
                Person(person_id="person_debbie", name="Debbie", birth_year=1964, birth_month=10)],
        entities=[Entity(entity_id="entity_phil", name="Phil", entity_type="personal"),
                  Entity(entity_id="entity_joint", name="Philip & Debbie Neufeld", entity_type="joint")],
        memberships=[Membership(entity_id="entity_phil", person_id="person_phil")],
    )


def link(at, entity_id, person_id, role="owner"):
    at.selectbox(key="link_entity").set_value(entity_id)
    at.selectbox(key="link_person").set_value(person_id)
    at.selectbox(key="link_role").set_value(role)
    return click(at, key="link_add")


# --- personal entity with a birthday ---------------------------------------------------------------

def test_birthday_on_a_new_personal_entity_creates_the_person_and_owner_link(tmp_path):
    at = start(Portfolio(), tmp_path)
    at = click(at, key="entity_add_new_btn")
    editor_widget(at, at.text_input, "entity", "name").input("Phil")
    editor_widget(at, at.number_input, "entity", "birth_year").set_value(1965)
    editor_widget(at, at.selectbox, "entity", "birth_month").set_value(2).run()
    assert any("Age in" in i.value and "**61**" in i.value for i in at.info)  # live age while typing
    at = click(at, label="💾 Save entity")
    no_crash(at)

    p = stored(at)
    (person, role), = p.people_in("entity_phil")
    assert (person.name, person.birth_year, person.birth_month, role.value) == ("Phil", 1965, 2, "owner")
    row = table(at, "People (role)").iloc[0]
    assert (row["People (role)"], row["Born"]) == ("Phil (owner)", "Feb 1965")


def test_saving_the_personal_entity_again_updates_the_person_without_a_second_link(tmp_path):
    at = start(couple(), tmp_path)
    at.session_state["_editor_entity"] = {"mode": "edit", "record_id": "entity_phil"}  # = Edit selected
    at.run()
    editor_widget(at, at.number_input, "entity", "birth_year").set_value(1966).run()
    at = click(at, label="💾 Save entity")
    no_crash(at)
    p = stored(at)
    assert p.get_person("person_phil").birth_year == 1966
    assert len([m for m in p.memberships if m.entity_id == "entity_phil"]) == 1


# --- linking people and entities ------------------------------------------------------------------------

def test_linking_both_spouses_shows_in_both_directions(tmp_path):
    at = start(couple(), tmp_path)
    at = link(at, "entity_joint", "person_phil")
    at = link(at, "entity_joint", "person_debbie")
    no_crash(at)

    entities = table(at, "People (role)").set_index("Name")
    assert entities.loc["Philip & Debbie Neufeld", "People (role)"] == "Phil (owner), Debbie (owner)"
    people = table(at, "Entities (role)").set_index("Name")
    assert people.loc["Phil", "Entities (role)"] == "Phil (owner); Philip & Debbie Neufeld (owner)"
    assert people.loc["Debbie", "Entities (role)"] == "Philip & Debbie Neufeld (owner)"


def test_duplicate_link_shows_an_error_instead_of_crashing(tmp_path):
    at = start(couple(), tmp_path)
    at = link(at, "entity_phil", "person_phil")  # already linked as owner
    no_crash(at)
    assert any("already has that role" in e.value for e in at.error)


def test_second_owner_on_a_personal_entity_is_refused_on_screen(tmp_path):
    at = start(couple(), tmp_path)
    at = link(at, "entity_phil", "person_debbie")
    no_crash(at)
    assert any("only one owner" in e.value for e in at.error)
    assert len(stored(at).people_in("entity_phil")) == 1


def test_create_personal_entity_button(tmp_path):
    at = start(couple(), tmp_path)
    at.selectbox(key="personal_entity_pick").set_value("person_debbie")
    at = click(at, key="personal_entity_create")
    no_crash(at)
    debbie_entities = [(e.entity_type.value, r.value) for e, r in stored(at).entities_of("person_debbie")]
    assert debbie_entities == [("personal", "owner")]


def test_changing_a_links_role(tmp_path):
    at = start(couple(), tmp_path)
    at = link(at, "entity_joint", "person_debbie")
    at.session_state["_editor_membership"] = {"mode": "edit", "record_id": "entity_joint|person_debbie|owner"}
    at.run()
    editor_widget(at, at.selectbox, "membership", "role").set_value("beneficiary")
    at = next(b for b in at.button if b.key and b.key.startswith("_editor_membership_save_")).click().run()
    no_crash(at)
    roles = [r.value for _, r in stored(at).entities_of("person_debbie")]
    assert roles == ["beneficiary"]


# --- ownership % ------------------------------------------------------------------------------------------

def test_ownership_form_explain_and_over_100_check(tmp_path):
    p = couple()
    p.entities += [Entity(entity_id="entity_lp", name="Neufeld Family LP", entity_type="partnership"),
                   Entity(entity_id="entity_nv", name="Neufeld Ventures", entity_type="llc"),
                   Entity(entity_id="entity_lvm", name="Loma Vista Marketplace", entity_type="llc")]
    at = start(p, tmp_path)

    def own(owner, owned, pct):
        at.selectbox(key="own_owner").set_value(owner)
        at.selectbox(key="own_type").set_value("entity").run()
        at.selectbox(key="own_owned").set_value(owned)
        at.number_input(key="own_pct").set_value(pct)
        return click(at, key="own_add")

    for owner, owned, pct in [("entity_joint", "entity_lp", 100.0), ("entity_joint", "entity_nv", 100.0),
                              ("entity_lp", "entity_lvm", 6.45), ("entity_nv", "entity_lvm", 1.0)]:
        at = own(owner, owned, pct)
    no_crash(at)
    assert stored(at).entity_share("entity_lvm") == pytest.approx(0.0745)

    at.selectbox(key="own_explain").set_value(("entity", "entity_lvm")).run()
    shown = [m.value for m in at.markdown]
    assert any("Neufeld Family LP → Loma Vista Marketplace: **6.4500%**" in s for s in shown)
    assert any("Total: **7.4500%**" in s for s in shown)

    at = own("entity_joint", "entity_lvm", 95.0)  # 7.45% + 95% > 100%
    no_crash(at)
    assert any("more than 100%" in e.value for e in at.error)
    assert stored(at).entity_share("entity_lvm") == pytest.approx(0.0745)  # unchanged


# --- actuals tab ------------------------------------------------------------------------------------------

def test_actuals_import_review_and_report(tmp_path):
    import actuals
    from test_actuals import CSV, household

    p = household()
    at = start(p, tmp_path)
    # Reading a file = parse + match; put the preview where the "Read file" button puts it.
    at.session_state["actuals_preview"] = actuals.match_rows(actuals.parse_csv(CSV), p)
    at.run()
    review = table(at, "Planned cashflow")
    assert list(review["Planned cashflow"])[:3] == ["Pistachio crop sales (Pistachios)"] * 2 + ["Brix rent (4392 Brix)"]
    assert review["Planned cashflow"].iloc[5] == "(not in the plan)"

    at = click(at, key="actuals_import")
    no_crash(at)
    assert len(stored(at).actuals) == 6
    assert any("Imported 6 record(s)" in s.value for s in at.success)

    report = table(at, "Difference %").set_index("Item")
    assert report.loc["Pistachio crop sales", "Planned"] == "$130,000"
    assert report.loc["Pistachio crop sales", "Actual / forecast"] == "$118,500"
    assert report.loc["Well pump repair", "Flag"] == "⚠"

    at.selectbox(key="pva_year").set_value(2027).run()
    breaks = table(at, "Gap %").set_index("Item")
    assert breaks.loc["Pistachio crop sales", "Flag"] == "⚠"  # 150,000 vs 120,870 expected
    no_crash(at)


def test_scenario_can_switch_actuals_off(tmp_path):
    from test_actuals import household
    import actuals
    from test_actuals import CSV

    p = household()
    actuals.commit_rows(actuals.match_rows(actuals.parse_csv(CSV), p), p)
    at = start(p, tmp_path)
    at = click(at, label="Run simulation for all scenarios")
    no_crash(at)
    scenarios_table = table(at, "Uses actuals").set_index("Name")
    assert bool(scenarios_table.loc["Plan only", "Uses actuals"]) is False


# --- taxes ---------------------------------------------------------------------------------------------------

def _depreciating_rental():
    from test_taxes import FlowType, TaxCategory, flow, household, rental, wages
    house = rental(depreciable_basis=275_000, placed_in_service_year=2000, recovery_years=27.5)
    return household([wages(100_000),
                      flow("rent", 30_000, FlowType.RENT_INCOME, TaxCategory.RENTAL_INCOME, asset_id="rental"),
                      flow("costs", 8_000, FlowType.OPERATING_EXPENSE, out=True, asset_id="rental")],
                     assets=[house], years=3)


def test_taxes_by_year_shows_depreciation_running_out(tmp_path):
    at = start(_depreciating_rental(), tmp_path)
    at = click(at, label="Run simulation for all scenarios")
    no_crash(at)
    by_year = table(at, "Taxable income").set_index("Year")
    assert list(by_year["Depreciation"]) == ["$10,000", "$5,000", "$0"]
    assert list(by_year["Taxable income"]) == ["$79,800", "$84,800", "$89,800"]


def test_switching_to_the_flat_method_on_the_assumptions_tab(tmp_path):
    at = start(_depreciating_rental(), tmp_path)
    at.selectbox(key="assump_tax_method").set_value("flat")
    at = click(at, label="Update assumptions")
    no_crash(at)
    assert stored(at).assumptions.tax_method == "flat"
    assert stored(at).assumptions.filing_status == "married_joint"  # other settings kept
    at = click(at, label="Run simulation for all scenarios")
    assert not any("Taxable income" in d.value.columns for d in at.dataframe)  # no detail table


def test_depreciation_is_saved_from_the_asset_editor(tmp_path):
    at = start(_depreciating_rental(), tmp_path)
    at.session_state["_editor_asset"] = {"mode": "edit", "record_id": "rental"}
    at.run()
    editor_widget(at, at.number_input, "asset", "depreciable_basis").set_value(390_000)
    editor_widget(at, at.number_input, "asset", "recovery_years").set_value(39.0)
    at = next(b for b in at.button if b.label == "💾 Save").click().run()
    no_crash(at)
    house = stored(at).get_asset("rental")
    assert (house.depreciable_basis, house.recovery_years, house.placed_in_service_year) == (390_000, 39.0, 2000)
    assert house.depreciation_in_year(2026) == pytest.approx(10_000)


# --- rentals with a mortgage --------------------------------------------------------------------------

def _empty_plan():
    from models import Assumptions, Scenario
    return Portfolio(assumptions=Assumptions(start_year=2026, end_year=2028, inflation_rate=0.0),
                     scenarios=[Scenario(scenario_id="base", name="Base", is_base_case=True)])


def test_add_rental_form_builds_property_and_mortgage_then_pro_forma_shows(tmp_path):
    at = start(_empty_plan(), tmp_path)
    at.text_input(key="rental_name").set_value("4392 N Brix")
    at.number_input(key="rental_value").set_value(400_000.0)
    at.number_input(key="rental_rent").set_value(24_000.0)
    at.number_input(key="rental_rgrowth").set_value(0.0)
    at.number_input(key="rental_egrowth").set_value(0.0)
    at.number_input(key="rental_exp_property_tax").set_value(4_000.0)
    at.number_input(key="rental_exp_insurance").set_value(1_200.0)
    at.number_input(key="rental_exp_repairs").set_value(1_800.0)
    at.number_input(key="rental_exp_management").set_value(1_920.0)
    at.number_input(key="rental_orig").set_value(300_000.0)
    at.number_input(key="rental_fpy").set_value(2016)
    at = click(at, label="Add rental")
    no_crash(at)
    p = stored(at)
    assert [a.name for a in p.assets] == ["4392 N Brix"]
    loan = p.liabilities[0]
    assert loan.balance == pytest.approx(251_057.17, abs=0.01) and loan.term_months == 240
    assert any("240 (20.0 yrs; last Dec 2045)" in s.value for s in at.success)

    at = click(at, label="Run simulation for all scenarios")
    no_crash(at)
    pf = next(d.value for d in at.dataframe if "2026" in d.value.columns and "Net operating income (NOI)" in d.value.index)
    assert pf.loc["Gross operating income (GOI)", "2026"] == "$22,800"   # 24,000 less 5% vacancy
    assert pf.loc["Net operating income (NOI)", "2026"] == "$13,880"
    assert pf.loc["Debt service", "2026"] == "$21,584"


def test_loan_editor_works_out_balance_from_original_terms(tmp_path):
    at = start(_empty_plan(), tmp_path)
    at = click(at, key="liability_add_new_btn")
    editor_widget(at, at.text_input, "liability", "name").set_value("Brix mortgage")
    editor_widget(at, at.number_input, "liability", "rate").set_value(6.0)
    editor_widget(at, at.number_input, "liability", "original_amount").set_value(300_000.0)
    editor_widget(at, at.number_input, "liability", "original_years").set_value(30.0)
    editor_widget(at, at.number_input, "liability", "first_year").set_value(2016)
    editor_widget(at, at.checkbox, "liability", "use_original").check()
    at = next(b for b in at.button if b.label == "💾 Save").click().run()
    no_crash(at)
    loan = stored(at).liabilities[0]
    assert loan.balance == pytest.approx(251_057.17, abs=0.01)
    assert (loan.term_months, loan.original_amount, loan.origination_year) == (240, 300_000, 2016)
    assert table(at, "Payments left").iloc[0]["Payments left"] == "240 (20.0 yrs; last Dec 2045)"
