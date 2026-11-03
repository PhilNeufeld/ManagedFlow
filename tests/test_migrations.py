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
    expected = {
        cf["flow_id"]: a["asset_id"] for a in v0_1_data["assets"] for cf in a["cashflows"]
    }
    new = migrate(v0_1_data)
    linked = {cf["flow_id"]: cf["asset_id"] for cf in new["cashflows"] if cf["asset_id"]}
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
