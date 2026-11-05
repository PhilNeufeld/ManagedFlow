"""Old files must keep loading after the model changes."""
import copy

import pytest

from migrations import CURRENT_SCHEMA_VERSION, MigrationError, migrate


def test_v0_1_cashflows_move_to_one_top_level_list(v0_1_data):
    nested = sum(len(a.get("cashflows", [])) for a in v0_1_data["assets"])
    household = len(v0_1_data["household_cashflows"])

    new = migrate(v0_1_data)

    assert new["metadata"]["file_version"] == CURRENT_SCHEMA_VERSION
    assert "household_cashflows" not in new
    assert all("cashflows" not in a for a in new["assets"])
    assert len(new["cashflows"]) == nested + household


def test_asset_flows_get_linked_by_asset_id(v0_1_data):
    # compared by name: the migration also renumbers every key (v1.0 integer keys)
    expected = {cf["name"]: a["name"] for a in v0_1_data["assets"] for cf in a["cashflows"]}
    new = migrate(v0_1_data)
    asset_names = {a["asset_id"]: a["name"] for a in new["assets"]}
    linked = {cf["name"]: asset_names[cf["asset_id"]] for cf in new["cashflows"] if cf["asset_id"]}
    assert linked == expected


def test_bundle_field_renamed(v0_1_data):
    new = migrate(v0_1_data)
    for b in new["bundles"]:
        assert "standalone_cashflow_ids" not in b
        assert "cashflow_ids" in b


def test_migrate_does_not_change_the_callers_dict(v0_1_data):
    before = copy.deepcopy(v0_1_data)
    migrate(v0_1_data)
    assert v0_1_data == before


def test_migrating_current_data_is_a_no_op(v0_1_data):
    once = migrate(v0_1_data)
    assert migrate(once) == once


def test_unknown_version_is_rejected(v0_1_data):
    v0_1_data["metadata"]["file_version"] = "9.9"
    with pytest.raises(MigrationError):
        migrate(v0_1_data)


# --- v1.0: integer primary keys ---------------------------------------------------------------------

def test_every_key_becomes_an_integer_and_links_follow(v0_1_data):
    from storage import load_portfolio  # noqa: F401  (keeps the import style of this file)
    new = migrate(v0_1_data)
    for table, pk in (("assets", "asset_id"), ("cashflows", "flow_id"), ("entities", "entity_id"),
                      ("scenarios", "scenario_id"), ("bundles", "bundle_id")):
        keys = [r[pk] for r in new[table]]
        assert all(isinstance(k, int) for k in keys) and len(set(keys)) == len(keys) and min(keys or [1]) == 1
    asset_keys = {a["asset_id"] for a in new["assets"]}
    assert all(cf["asset_id"] in asset_keys for cf in new["cashflows"] if cf["asset_id"] is not None)
    for b in new["bundles"]:
        assert set(b["asset_ids"]) <= asset_keys


def test_overrides_keyed_by_records_are_renumbered_and_survive_json():
    import json
    from migrations import to_integer_keys
    data = {"assets": [{"asset_id": "house"}, {"asset_id": "farm"}],
            "cashflows": [{"flow_id": "rent", "asset_id": "house"}],
            "events": [{"event_id": "sell"}],
            "scenarios": [{"scenario_id": "base", "asset_overrides": {"farm": {"sale_year": 2030}},
                           "cashflow_overrides": {"rent": {"amount": 1}}, "event_overrides": {"sell": {"year": 2031}},
                           "entity_ids": [], "bundle_ids": []}]}
    out = to_integer_keys(data)
    s = out["scenarios"][0]
    assert s["scenario_id"] == 1 and s["asset_overrides"] == {2: {"sale_year": 2030}}
    assert s["cashflow_overrides"] == {1: {"amount": 1}} and s["event_overrides"] == {1: {"year": 2031}}
    assert out["cashflows"][0]["asset_id"] == 1
    # JSON turns 2 into "2"; the Scenario model turns it back into a key
    from models import Scenario
    back = Scenario(scenario_id=1, name="b", asset_overrides=json.loads(json.dumps(s["asset_overrides"])))
    assert back.asset_overrides == {2: {"sale_year": 2030}}


def test_integer_keys_already_present_are_kept():
    from migrations import to_integer_keys
    out = to_integer_keys({"assets": [{"asset_id": 5}, {"asset_id": "new"}]})
    assert [a["asset_id"] for a in out["assets"]] == [5, 1]


def test_next_id_is_one_more_than_the_largest():
    from models import Asset, Portfolio
    p = Portfolio(assets=[Asset(asset_id=3, name="a", asset_type="cash", owner="x", initial_value=0, start_year=2026),
                          Asset(asset_id=7, name="b", asset_type="cash", owner="x", initial_value=0, start_year=2026)])
    assert p.next_id("asset") == 8 and p.next_id("cashflow") == 1
