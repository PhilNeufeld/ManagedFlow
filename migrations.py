"""
migrations.py

Upgrades portfolio JSON written by older versions of ManagedFlow to the
current schema, so changing the data model never strands a saved file.

How it works
------------
Every saved file records its schema in metadata.file_version. migrate()
looks at that version and applies each upgrade step in order until the
data is current -- like climbing a ladder one rung at a time:

    "0.1" --(_v0_1_to_v0_2)--> "0.2"

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

CURRENT_SCHEMA_VERSION = "0.2"


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


# version found in the file -> function that upgrades it one step
_STEPS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "0.1": _v0_1_to_v0_2,
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
