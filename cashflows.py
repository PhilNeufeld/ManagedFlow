"""
cashflows.py

Withdrawal / liquidity rules engine.

When household cash available falls short of household cash needed in a
simulation year, this module decides which assets to draw down from, in
what order, respecting each asset's liquidity_category and do_not_sell flag.

This is deliberately simple in the MVP: it draws down asset *value*
(current_value) directly rather than modeling actual security sales,
withdrawal fees, or tax consequences of the withdrawal itself (those
consequences, if any, belong in taxes.py / future scenario rules). The
goal is a transparent, auditable shortfall-covering mechanism, not a
brokerage simulator.
"""

from __future__ import annotations

from typing import Dict, List

from models import Asset, LiquidityCategory, Scenario


DEFAULT_WITHDRAWAL_ORDER = [
    LiquidityCategory.LIQUID.value,
    LiquidityCategory.SEMI_LIQUID.value,
    LiquidityCategory.RESTRICTED.value,
    LiquidityCategory.ILLIQUID.value,
    LiquidityCategory.INCOME_ONLY.value,
    LiquidityCategory.DO_NOT_SELL.value,  # only ever reached if a scenario explicitly allows it
]


def withdraw_for_shortfall(
    shortfall: float,
    assets: List[Asset],
    scenario: Scenario,
) -> Dict[str, float]:
    """Attempt to cover `shortfall` (a positive dollar amount) by drawing
    down asset current_value in liquidity order, mutating each asset's
    current_value in place.

    Returns a dict of {asset_id: amount_withdrawn}. If the shortfall cannot
    be fully covered (assets exhausted), the returned dict's total will be
    less than `shortfall` -- callers should check for this and surface a
    warning rather than assuming the shortfall was met.

    Rules:
    - Inactive assets are skipped.
    - Assets with do_not_sell=True are skipped unless
      scenario.allow_illiquid_withdrawals is True.
    - Order follows DEFAULT_WITHDRAWAL_ORDER unless the scenario provides
      scenario.withdrawal_order_override (a list of liquidity_category
      string values, evaluated in order).
    """
    if shortfall <= 0:
        return {}

    order = scenario.withdrawal_order_override or DEFAULT_WITHDRAWAL_ORDER
    remaining = shortfall
    withdrawals: Dict[str, float] = {}

    for liquidity_value in order:
        if remaining <= 0:
            break

        candidates = [
            a
            for a in assets
            if a.active
            and a.asset_type.value != "receivable"      # its value is the balance owed, not cash to draw
            and a.liquidity_category.value == liquidity_value
            and (not a.do_not_sell or scenario.allow_illiquid_withdrawals)
            and a.current_value > 0
        ]

        # Within a liquidity group, draw in tax order: taxable accounts first, then other
        # assets, then tax-deferred (withdrawals are income), Roth last (tax-free growth).
        tax_order = {"taxable": 0, "": 1, "tax_deferred": 2, "roth": 3}
        candidates.sort(key=lambda a: tax_order.get(getattr(a, "account_tax", ""), 1))
        for asset in candidates:
            if remaining <= 0:
                break
            draw = min(asset.current_value, remaining)
            if draw <= 0:
                continue
            asset.current_value -= draw
            remaining -= draw
            withdrawals[asset.asset_id] = withdrawals.get(asset.asset_id, 0.0) + draw

    return withdrawals


def reinvest_surplus(
    surplus: float,
    assets: List[Asset],
    scenario: Scenario,
    target_liquidity: str = LiquidityCategory.LIQUID.value,
) -> Dict[str, float]:
    """Reinvest a positive surplus into the first active asset matching
    target_liquidity (typically the household's primary liquid/cash asset),
    if scenario.reinvest_surplus is True.

    Returns {asset_id: amount_reinvested}, which will be empty if
    reinvest_surplus is disabled, there is no surplus, or no matching asset
    exists (in which case the surplus is simply left untouched -- it still
    shows up in AnnualHouseholdResult.surplus_or_deficit for visibility).
    """
    if surplus <= 0 or not scenario.reinvest_surplus:
        return {}

    for asset in assets:
        if asset.active and asset.liquidity_category.value == target_liquidity:
            asset.current_value += surplus
            return {asset.asset_id: surplus}

    return {}
