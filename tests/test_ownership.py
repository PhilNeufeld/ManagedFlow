"""Ownership %: your share flows down the ownership chain.

The structure is yours; dollar amounts are round illustrative numbers.

    Alex & Sam (joint) --100%--> Rivera Family LP --6.45%--> Plaza
                            --100%--> Summit Ventures  --1%-----> Plaza
                            --100%--> Family Trust 2000 --14.75%-> Lakeside
                            --100%--> Oak Hill          --33.3%--> 88 Market St (asset)
                                                        holds ---> 12 Elm St (asset, 100%)
"""
import pytest

import simulation
from migrations import migrate
from models import (
    Asset, Assumptions, Cashflow, CashflowDirection, Entity, FlowType, Loan, Ownership, Portfolio, Scenario,
    ValidationError,
)


def owns(owner, owned, pct, owned_type="entity"):
    return Ownership(owner_entity_id=owner, owned_type=owned_type, owned_id=owned, percent=pct)


def neufeld():
    entities = [Entity(entity_id=e, name=n, entity_type=t) for e, n, t in [
        ("joint", "Alex & Sam Rivera", "joint"), ("lp", "Rivera Family LP", "partnership"),
        ("nv", "Summit Ventures", "llc"), ("plaza", "Harbor Plaza", "llc"),
        ("trust", "Rivera Family Trust 2000", "trust"), ("lakeview", "Lakeside Partners", "llc"),
        ("oakhill", "Oak Hill Investments", "llc")]]
    return Portfolio(
        assumptions=Assumptions(start_year=2026, end_year=2027, inflation_rate=0.0),
        entities=entities,
        assets=[
            Asset(asset_id="plaza_center", name="Plaza shopping center", asset_type="real_estate", owner="Plaza",
                  initial_value=3_000_000, start_year=2026, entity_id="plaza"),
            Asset(asset_id="broadway", name="88 Market St", asset_type="real_estate", owner="Oak Hill",
                  initial_value=700_000, start_year=2026),
            Asset(asset_id="elm", name="12 Elm St", asset_type="real_estate", owner="Oak Hill",
                  initial_value=300_000, start_year=2026, entity_id="oakhill"),
        ],
        liabilities=[Loan(liability_id="plaza_loan", name="Plaza construction loan", balance=1_000_000,
                          annual_interest_rate=0.06, start_year=2026, term_months=300,
                          secured_by_asset_id="plaza_center")],
        cashflows=[Cashflow(flow_id="plaza_dist", name="Plaza distribution", flow_type=FlowType.BUSINESS_DISTRIBUTION,
                            direction=CashflowDirection.INFLOW, amount=200_000, start_year=2026,
                            asset_id="plaza_center"),
                   Cashflow(flow_id="salary", name="Consulting", flow_type=FlowType.SALARY_INCOME,
                            direction=CashflowDirection.INFLOW, amount=50_000, start_year=2026)],
        ownerships=[owns("joint", "lp", 1.0), owns("joint", "nv", 1.0), owns("joint", "trust", 1.0),
                    owns("joint", "oakhill", 1.0), owns("lp", "plaza", 0.0645), owns("nv", "plaza", 0.01),
                    owns("trust", "lakeview", 0.1475), owns("oakhill", "broadway", 0.333, "asset")],
        scenarios=[Scenario(scenario_id="share", name="Your share"),
                   Scenario(scenario_id="full", name="100% view", ownership_view="full")],
    )


# --- the share math ---------------------------------------------------------------------

def test_plaza_is_two_paths_added_together():
    assert neufeld().entity_share("plaza") == pytest.approx(0.0645 + 0.01)  # 7.45%


def test_lakeview_through_the_trust():
    assert neufeld().entity_share("lakeview") == pytest.approx(0.1475)


def test_unowned_entity_is_all_yours():
    assert neufeld().entity_share("joint") == 1.0


def test_asset_owned_directly_by_percent():
    assert neufeld().asset_share("broadway") == pytest.approx(0.333)


def test_asset_follows_its_holding_entity():
    p = neufeld()
    assert p.asset_share("elm") == 1.0                       # Oak Hill is 100% yours
    assert p.asset_share("plaza_center") == pytest.approx(0.0745)


def test_paths_explain_the_share():
    paths = sorted((" > ".join(path), round(share, 6)) for path, share in neufeld().share_paths("entity", "plaza"))
    assert paths == [
        ("Alex & Sam Rivera > Rivera Family LP > Harbor Plaza", 0.0645),
        ("Alex & Sam Rivera > Summit Ventures > Harbor Plaza", 0.01),
    ]


# --- in the simulation ------------------------------------------------------------------------

def first_year(p, scenario_id):
    return simulation.run_simulation(p, p.get_scenario(scenario_id)).annual_results[0]


def test_your_share_scales_value_income_and_debt():
    year = first_year(neufeld(), "share")
    values = {a.asset_id: a.beginning_value for a in year.asset_results}
    assert values["plaza_center"] == pytest.approx(3_000_000 * 0.0745)   # 223,500
    assert values["broadway"] == pytest.approx(700_000 * 0.333)        # 233,100
    assert values["elm"] == pytest.approx(300_000)
    # 7.45% of the $200,000 distribution + the household's own $50,000 (not owned by an entity)
    assert year.total_gross_income == pytest.approx(200_000 * 0.0745 + 50_000)
    assert year.liability_results[0].beginning_balance == pytest.approx(1_000_000 * 0.0745)


def test_full_view_ignores_ownership():
    year = first_year(neufeld(), "full")
    values = {a.asset_id: a.beginning_value for a in year.asset_results}
    assert values["plaza_center"] == 3_000_000
    assert year.total_gross_income == pytest.approx(250_000)
    assert year.liability_results[0].beginning_balance == 1_000_000


def test_share_of_a_loan_pays_the_same_share_of_the_payment():
    share, full = first_year(neufeld(), "share"), first_year(neufeld(), "full")
    assert share.total_debt_service == pytest.approx(full.total_debt_service * 0.0745)


def test_stored_portfolio_is_never_scaled():
    p = neufeld()
    first_year(p, "share")
    assert p.get_asset("plaza_center").initial_value == 3_000_000


# --- safety checks ------------------------------------------------------------------------------

def test_over_100_percent_is_rejected():
    with pytest.raises(ValidationError):
        neufeld().add_ownership(owns("trust", "plaza", 0.95))  # 7.45% + 95% > 100%


def test_ownership_loop_is_rejected():
    with pytest.raises(ValidationError):
        neufeld().add_ownership(owns("plaza", "joint", 0.5))   # joint -> ... -> Plaza -> joint


def test_percent_must_be_a_fraction():
    with pytest.raises(ValidationError):
        owns("joint", "lp", 6.45)


def test_removing_an_entity_removes_its_ownership_links():
    p = neufeld()
    p.remove_entity("nv")
    assert p.entity_share("plaza") == pytest.approx(0.0645)


def test_ownerships_survive_save_and_load():
    p = neufeld()
    assert Portfolio.from_dict(p.to_dict()).to_dict() == p.to_dict()


def test_old_files_get_no_ownerships_and_the_share_view(v0_1_data):
    new = migrate(v0_1_data)
    assert new["ownerships"] == []
    assert all(s["ownership_view"] == "share" for s in new["scenarios"])


# --- primary keys on the link table (v1.1) -------------------------------------------------------------

def test_ownership_links_have_integer_keys_and_edit_in_place():
    from models import Ownership
    p = Portfolio(entities=[Entity(entity_id=1, name="Us"), Entity(entity_id=2, name="LP", entity_type="partnership")])
    link = Ownership(owner_entity_id=1, owned_type="entity", owned_id=2, percent=0.2)
    p.add_ownership(link)
    assert link.ownership_id == 1
    p.replace_ownership(1, Ownership(owner_entity_id=1, owned_type="entity", owned_id=2, percent=0.25))
    assert p.get_ownership(1).percent == 0.25 and p.entity_share(2) == pytest.approx(0.25)
    with pytest.raises(ValidationError):                      # the owner/owned pair stays unique
        p.add_ownership(Ownership(owner_entity_id=1, owned_type="entity", owned_id=2, percent=0.1))
    p.remove_ownership(1)
    assert not p.ownerships
