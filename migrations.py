"""
migrations.py

Upgrades portfolio JSON written by older versions of ManagedFlow to the
current schema, so changing the data model never strands a saved file.

How it works
------------
Every saved file records its schema in metadata.file_version. migrate()
looks at that version and applies each upgrade step in order until the
data is current -- like climbing a ladder one rung at a time:

    "0.1" --(_v0_1_to_v0_2)--> "0.2" --(_v0_2_to_v0_3)--> "0.3" --(_v0_3_to_v0_4)--> "0.4" --(_v0_4_to_v0_5)--> "0.5" --(_v0_5_to_v0_6)--> "0.6" --(_v0_6_to_v0_7)--> "0.7" --(_v0_7_to_v0_8)--> "0.8" --(_v0_8_to_v0_9)--> "0.9" --(_v0_9_to_v1_0)--> "1.0" --(_v1_0_to_v1_1)--> "1.1"

To change the model in the future:
  1. Bump CURRENT_SCHEMA_VERSION.
  2. Write a _vX_to_vY(data) function that reshapes the raw dict.
  3. Register it in _STEPS.
  4. Add a test in tests/test_migrations.py.

This module works only on plain dicts (raw JSON), never on model classes,
so it can't break when the classes change.
"""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict

CURRENT_SCHEMA_VERSION = "1.1"


class MigrationError(ValueError):
    """Raised when a file's version can't be upgraded."""


def _v0_1_to_v0_2(data: Dict[str, Any]) -> Dict[str, Any]:
    """v0.1 kept cashflows in two places: household_cashflows at the top
    level, and a cashflows list nested inside each asset. v0.2 keeps ONE
    top-level 'cashflows' list; each cashflow links to its asset by
    asset_id (or has asset_id None for household-level flows like salary).

    Bundles' 'standalone_cashflow_ids' is renamed to 'cashflow_ids'.

    Order is preserved (household flows first, then each asset's flows in
    asset order) so simulation totals come out identical.
    """
    flows = list(data.pop("household_cashflows", []) or [])
    for asset in data.get("assets", []) or []:
        for cf in asset.pop("cashflows", []) or []:
            cf["asset_id"] = asset.get("asset_id")
            flows.append(cf)
    data["cashflows"] = flows

    for bundle in data.get("bundles", []) or []:
        if "standalone_cashflow_ids" in bundle:
            bundle["cashflow_ids"] = bundle.pop("standalone_cashflow_ids")

    data.setdefault("metadata", {})["file_version"] = "0.2"
    return data


def _v0_2_to_v0_3(data: Dict[str, Any]) -> Dict[str, Any]:
    """v0.3 adds liabilities (loans, credit lines) as a top-level list,
    and bundles can now hold liability_ids. Older files had no liabilities,
    so both start empty."""
    data.setdefault("liabilities", [])
    for bundle in data.get("bundles", []) or []:
        bundle.setdefault("liability_ids", [])
    data.setdefault("metadata", {})["file_version"] = "0.3"
    return data


def _v0_3_to_v0_4(data: Dict[str, Any]) -> Dict[str, Any]:
    """v0.4 adds Events (named dates that records link to) and month-level
    start/end dates on cashflows. Older files have no events; all new
    record fields have defaults (months 1-12 = whole years), so nothing
    else needs to change."""
    data.setdefault("events", [])
    data.setdefault("metadata", {})["file_version"] = "0.4"
    return data


def _v0_4_to_v0_5(data: Dict[str, Any]) -> Dict[str, Any]:
    """v0.5 adds People (birth year/month, life expectancy) and age-based
    events. Household.members (plain names) is kept as-is: birthdays can't
    be invented, so people are added by the user."""
    data.setdefault("people", [])
    data.setdefault("metadata", {})["file_version"] = "0.5"
    return data


def _v0_5_to_v0_6(data: Dict[str, Any]) -> Dict[str, Any]:
    """v0.6 links people and entities many-to-many through Memberships
    (entity, person, role). A brief interim version stored a single
    Entity.person_id; any such link becomes an 'owner' membership."""
    memberships = data.setdefault("memberships", [])
    for entity in data.get("entities", []) or []:
        person_id = entity.pop("person_id", None)
        if person_id:
            memberships.append({"entity_id": entity["entity_id"], "person_id": person_id,
                                "role": "owner", "notes": ""})
    data.setdefault("metadata", {})["file_version"] = "0.6"
    return data


def _v0_6_to_v0_7(data: Dict[str, Any]) -> Dict[str, Any]:
    """v0.7 adds ownership % (Ownership links) and a per-scenario 100% view.
    No links yet means every share is 100%, so results are unchanged."""
    data.setdefault("ownerships", [])
    for scenario in data.get("scenarios", []) or []:
        scenario.setdefault("ownership_view", "share")
    data.setdefault("metadata", {})["file_version"] = "0.7"
    return data


def _v0_7_to_v0_8(data: Dict[str, Any]) -> Dict[str, Any]:
    """v0.8 adds Actuals (what really happened / near-term forecasts) and a
    per-scenario switch to use them in place of the plan."""
    data.setdefault("actuals", [])
    for scenario in data.get("scenarios", []) or []:
        scenario.setdefault("use_actuals", True)
    data.setdefault("metadata", {})["file_version"] = "0.8"
    return data


def _v0_8_to_v0_9(data: Dict[str, Any]) -> Dict[str, Any]:
    """v0.9 adds the detailed tax engine. Files saved before it keep the flat
    method (so their numbers don't change by surprise) until switched on the
    Assumptions tab. Depreciation fields on assets start empty."""
    assumptions = data.setdefault("assumptions", {})
    assumptions.setdefault("tax_method", "flat")
    assumptions.setdefault("filing_status", "married_joint")
    assumptions.setdefault("state", "CA")
    data.setdefault("metadata", {})["file_version"] = "0.9"
    return data


# Tables with an integer primary key: list name -> primary-key field.
PK_FIELDS = {
    "entities": "entity_id", "people": "person_id", "events": "event_id", "assets": "asset_id",
    "cashflows": "flow_id", "liabilities": "liability_id", "actuals": "actual_id",
    "bundles": "bundle_id", "scenarios": "scenario_id",
    "memberships": "membership_id", "ownerships": "ownership_id",
}
# Foreign keys: list name -> {field: table it points to}
FK_FIELDS = {
    "events": {"person_id": "people"},
    "assets": {"entity_id": "entities", "start_event_id": "events", "end_event_id": "events",
               "earner_person_id": "people"},
    "cashflows": {"asset_id": "assets", "start_event_id": "events", "end_event_id": "events",
                  "person_id": "people"},
    "liabilities": {"entity_id": "entities", "secured_by_asset_id": "assets", "start_event_id": "events",
                    "end_event_id": "events"},
    "memberships": {"entity_id": "entities", "person_id": "people"},
    "actuals": {"flow_id": "cashflows", "asset_id": "assets", "entity_id": "entities"},
    "bundles": {"entity_id": "entities"},
}
FK_LISTS = {   # list fields holding keys
    "bundles": {"asset_ids": "assets", "cashflow_ids": "cashflows", "liability_ids": "liabilities"},
    "scenarios": {"entity_ids": "entities", "bundle_ids": "bundles"},
}
FK_DICT_KEYS = {   # dicts keyed by another table's key
    "scenarios": {"asset_overrides": "assets", "cashflow_overrides": "cashflows", "event_overrides": "events"},
}


def to_integer_keys(data: Dict[str, Any]) -> Dict[str, Any]:
    """Give every record in every table an integer primary key (1, 2, 3... per
    table, in list order) and rewrite every foreign key to match. Keys that are
    already integers are kept. Works on the raw dict; used by the 0.9 -> 1.0
    migration and by scripts that build a portfolio with readable keys."""
    maps: Dict[str, Dict[Any, int]] = {}
    for table, pk in PK_FIELDS.items():
        rows = data.get(table) or []
        used = {r[pk] for r in rows if isinstance(r.get(pk), int)}
        nxt, m = 1, {}
        for r in rows:
            old = r.get(pk)
            if isinstance(old, int):
                m[old] = old
                continue
            while nxt in used:
                nxt += 1
            if old is None:            # no key yet (e.g. a link row before v1.1): just give it one
                r[pk] = nxt
            else:
                m[old] = nxt
            used.add(nxt)
        maps[table] = m
    remap = lambda table, v: maps[table].get(v, v) if v is not None else None  # noqa: E731
    for table, pk in PK_FIELDS.items():
        for r in data.get(table) or []:
            r[pk] = remap(table, r.get(pk))
    for table, fks in FK_FIELDS.items():
        for r in data.get(table) or []:
            for f, target in fks.items():
                if f in r:
                    r[f] = remap(target, r[f])
    for table, fks in FK_LISTS.items():
        for r in data.get(table) or []:
            for f, target in fks.items():
                r[f] = [remap(target, v) for v in r.get(f) or []]
    for table, fks in FK_DICT_KEYS.items():
        for r in data.get(table) or []:
            for f, target in fks.items():
                r[f] = {remap(target, _int_or(k)): v for k, v in (r.get(f) or {}).items()}
    for o in data.get("ownerships") or []:
        o["owner_entity_id"] = remap("entities", o.get("owner_entity_id"))
        o["owned_id"] = remap("entities" if o.get("owned_type") == "entity" else "assets", o.get("owned_id"))
    return data


def _int_or(k):
    """JSON object keys are always text: turn '12' back into 12."""
    return int(k) if isinstance(k, str) and k.isdigit() else k


def _v0_9_to_v1_0(data: Dict[str, Any]) -> Dict[str, Any]:
    """v1.0: integer primary keys in every table (they used to be text made
    from the name, like 'flow_phil_salary'); foreign keys point at them."""
    data = to_integer_keys(data)
    data.setdefault("metadata", {})["file_version"] = "1.0"
    return data


def _v1_0_to_v1_1(data: Dict[str, Any]) -> Dict[str, Any]:
    """v1.1: the link tables (memberships, ownerships) get integer primary keys too.
    Their column pairs stay unique (enforced by the model)."""
    data = to_integer_keys(data)
    data.setdefault("metadata", {})["file_version"] = "1.1"
    return data


# version found in the file -> function that upgrades it one step
_STEPS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "0.1": _v0_1_to_v0_2,
    "0.2": _v0_2_to_v0_3,
    "0.3": _v0_3_to_v0_4,
    "0.4": _v0_4_to_v0_5,
    "0.5": _v0_5_to_v0_6,
    "0.6": _v0_6_to_v0_7,
    "0.7": _v0_7_to_v0_8,
    "0.8": _v0_8_to_v0_9,
    "0.9": _v0_9_to_v1_0,
    "1.0": _v1_0_to_v1_1,
}


def file_version_of(data: Dict[str, Any]) -> str:
    return str((data.get("metadata") or {}).get("file_version", "0.1"))


def needs_migration(data: Dict[str, Any]) -> bool:
    return file_version_of(data) != CURRENT_SCHEMA_VERSION


def migrate(data: Dict[str, Any]) -> Dict[str, Any]:
    """Return an upgraded COPY of `data` at CURRENT_SCHEMA_VERSION.
    The caller's dict is never modified."""
    if not isinstance(data, dict):
        return data  # let storage validation report the problem
    data = copy.deepcopy(data)
    seen = set()
    while (version := file_version_of(data)) != CURRENT_SCHEMA_VERSION:
        if version in seen or version not in _STEPS:
            raise MigrationError(
                f"Don't know how to upgrade a portfolio file at version '{version}' "
                f"to '{CURRENT_SCHEMA_VERSION}'. Was it written by a newer ManagedFlow?"
            )
        seen.add(version)
        data = _STEPS[version](data)
    return data
