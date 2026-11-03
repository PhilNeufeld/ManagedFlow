"""
scenarios.py

Logic for applying a Scenario's overrides on top of a base Portfolio's
Assumptions, Assets, and Cashflows, producing an "effective" set of inputs
for a single simulation run. The base Portfolio on disk is never mutated --
this module always works on copies.

Supported override keys
------------------------
Scenario.asset_overrides[asset_id] may include any of:
    annual_growth_rate, annual_expense_rate, sale_year, do_not_sell,
    active, current_value

Scenario.cashflow_overrides[flow_id] may include any of:
    amount, start_year, end_year, annual_growth_rate, frequency, taxable

Unsupported keys are ignored with a warning rather than raising, so a
scenario file written for a future feature doesn't crash older code.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Tuple

from models import Assumptions, Portfolio, Scenario


SUPPORTED_ASSET_OVERRIDE_KEYS = {
    "annual_growth_rate",
    "annual_expense_rate",
    "sale_year",
    "do_not_sell",
    "active",
    "current_value",
}

SUPPORTED_CASHFLOW_OVERRIDE_KEYS = {
    "amount",
    "start_year",
    "end_year",
    "annual_growth_rate",
    "frequency",
    "taxable",
}


def apply_scenario(portfolio: Portfolio, scenario: Scenario) -> Tuple[Portfolio, List[str]]:
    """Return a deep-copied Portfolio with the scenario's overrides applied,
    plus a list of warning strings (e.g. for unknown override keys or
    references to asset/cashflow ids that don't exist).

    The original `portfolio` argument is never modified.

    Note: this function applies value overrides only. If the scenario also
    targets specific Entities/Bundles (scenario.entity_ids / bundle_ids),
    that scoping is resolved and applied separately by simulation.py via
    Portfolio.resolve_scope() -- overrides here are still applied to the
    full portfolio first, then simulation.py filters which assets/cashflows
    actually participate in the run.
    """
    working = copy.deepcopy(portfolio)
    warnings: List[str] = []

    _apply_assumption_overrides(working.assumptions, scenario, warnings)
    _apply_asset_overrides(working, scenario, warnings)
    _apply_cashflow_overrides(working, scenario, warnings)

    return working, warnings


def _apply_assumption_overrides(assumptions: Assumptions, scenario: Scenario, warnings: List[str]) -> None:
    if scenario.inflation_rate_override is not None:
        assumptions.inflation_rate = scenario.inflation_rate_override
    # expense_growth_rate_override is consumed directly by simulation.py when
    # growing flagged living-expense flows; nothing to do to Assumptions itself.


def _apply_asset_overrides(portfolio: Portfolio, scenario: Scenario, warnings: List[str]) -> None:
    for asset_id, overrides in scenario.asset_overrides.items():
        asset = portfolio.get_asset(asset_id)
        if asset is None:
            warnings.append(
                f"Scenario '{scenario.scenario_id}': asset_overrides references "
                f"unknown asset_id '{asset_id}' -- ignored"
            )
            continue

        for key, value in overrides.items():
            if key not in SUPPORTED_ASSET_OVERRIDE_KEYS:
                warnings.append(
                    f"Scenario '{scenario.scenario_id}': unsupported asset override "
                    f"key '{key}' for asset '{asset_id}' -- ignored"
                )
                continue
            setattr(asset, key, value)


def _apply_cashflow_overrides(portfolio: Portfolio, scenario: Scenario, warnings: List[str]) -> None:
    flows_by_id = {cf.flow_id: cf for cf in portfolio.all_cashflows()}

    for flow_id, overrides in scenario.cashflow_overrides.items():
        cashflow = flows_by_id.get(flow_id)
        if cashflow is None:
            warnings.append(
                f"Scenario '{scenario.scenario_id}': cashflow_overrides references "
                f"unknown flow_id '{flow_id}' -- ignored"
            )
            continue

        for key, value in overrides.items():
            if key not in SUPPORTED_CASHFLOW_OVERRIDE_KEYS:
                warnings.append(
                    f"Scenario '{scenario.scenario_id}': unsupported cashflow override "
                    f"key '{key}' for flow '{flow_id}' -- ignored"
                )
                continue
            setattr(cashflow, key, value)


def make_base_case_scenario() -> Scenario:
    """Convenience factory for a no-override Base Case scenario."""
    return Scenario(
        scenario_id="base_case",
        name="Base Case",
        description="No overrides; uses the portfolio's stored assumptions, assets, and cashflows as-is.",
        is_base_case=True,
    )
