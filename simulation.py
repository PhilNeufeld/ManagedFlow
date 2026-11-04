"""
simulation.py

The year-by-year simulation engine. This is the heart of the application:
everything else (storage, exports, the UI) revolves around producing and
consuming a SimulationResult from here.

Per-year sequence (matches the project spec's "Simulation Logic" section):
    1. For each asset: beginning value -> growth -> apply cashflows that
       affect value -> ending value. Track gross income/expense for
       reporting.
    2. For each liability: pay the year's interest and principal, reduce
       the balance (paid off in full if its collateral was sold this year).
       Debt service counts as an expense.
    3. Aggregate household-level cashflows (not tied to an asset).
    4. Estimate taxes for the year.
    5. Compute total cash available vs. total household expenses.
    6. Determine surplus/deficit.
    7. If deficit: run the withdrawal rules engine.
    8. If surplus: reinvest (if the scenario says to).
    9. Net worth = assets - liabilities. Record an AnnualHouseholdResult.

A "year" in this MVP is always a full calendar year. Monthly/quarterly
frequency cashflows are normalized to an annual figure inside
Cashflow.annual_amount_in_year(); the simulation does not sub-step within
a year.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from models import (
    Asset,
    AnnualAssetResult,
    AnnualHouseholdResult,
    AnnualLiabilityResult,
    Assumptions,
    Liability,
    CashflowDirection,
    Portfolio,
    Scenario,
    SimulationResult,
)
import cashflows as withdrawal_engine
import scenarios as scenario_engine
import social_security
import taxes as tax_engine


# A function (asset, year) -> growth rate for that asset in that year.
# None (the default) means "use each asset's own annual_growth_rate" --
# the normal, deterministic run. montecarlo.py passes one that adds random
# market swings.
GrowthRateFn = Callable[[Asset, int], float]


def run_simulation(
    portfolio: Portfolio, scenario: Scenario, growth_rate_fn: Optional[GrowthRateFn] = None
) -> SimulationResult:
    """Run a full year-by-year simulation for one scenario.

    Returns a SimulationResult containing one AnnualHouseholdResult per
    year in [assumptions.start_year, assumptions.end_year], inclusive.

    The input `portfolio` is never mutated -- apply_scenario() deep-copies
    it, and all per-year mutation (asset value growth, withdrawals,
    reinvestment) happens on that working copy.

    If the scenario targets specific Entities and/or Bundles (via
    scenario.entity_ids / scenario.bundle_ids), the simulation is scoped to
    just the Assets and household Cashflows those Bundles contain -- e.g.
    "run this scenario for just the LLC" or "just Rental Address 1."
    Leaving both empty (the default) runs the whole portfolio, unchanged
    from prior behavior.
    """
    working_portfolio, scenario_warnings = scenario_engine.apply_scenario(portfolio, scenario)
    assumptions = working_portfolio.assumptions

    asset_id_scope, flow_id_scope, liability_id_scope = working_portfolio.resolve_scope(
        scenario.entity_ids, scenario.bundle_ids
    )
    # Split the portfolio's single cashflow list into the two groups the
    # yearly engine needs: flows linked to an asset (grouped by asset_id)
    # and household-level flows (asset_id is None).
    flows_by_asset: dict[str, list] = {}
    household_flows = []
    for cf in working_portfolio.cashflows:
        if cf.asset_id is None:
            household_flows.append(cf)
        else:
            flows_by_asset.setdefault(cf.asset_id, []).append(cf)

    if asset_id_scope is not None:
        scope_assets = [a for a in working_portfolio.assets if a.asset_id in asset_id_scope]
        scope_household_cashflows = [cf for cf in household_flows if cf.flow_id in flow_id_scope]
        # A debt is in scope if its bundle names it, or if it's secured by
        # an asset that's in scope (the mortgage travels with the house).
        scope_liabilities = [
            li for li in working_portfolio.liabilities
            if li.liability_id in liability_id_scope or li.secured_by_asset_id in asset_id_scope
        ]
    else:
        scope_assets = working_portfolio.assets
        scope_household_cashflows = household_flows
        scope_liabilities = working_portfolio.liabilities

    # Running balance of each debt, carried from one year to the next.
    # (The Liability objects themselves keep their starting balance, which
    # Loan needs to compute its fixed payment.)
    balances = {li.liability_id: li.balance for li in scope_liabilities}

    annual_results: List[AnnualHouseholdResult] = []

    # Warn if the plan stops before someone's planning life expectancy.
    for person in working_portfolio.people:
        if person.final_year > assumptions.end_year:
            scenario_warnings.append(
                f"The plan ends in {assumptions.end_year}, when {person.name} is "
                f"{person.age_in(assumptions.end_year)}; planning to age {person.life_expectancy} "
                f"would run to {person.final_year}. Consider extending the end year."
            )

    tax_state: dict = {}  # carried from year to year (e.g. suspended rental losses)
    # Benefits recorded as ACTUALS are what was really paid: the earnings test leaves them alone.
    actual_slots = ({(a.flow_id, a.year) for a in working_portfolio.actuals if a.kind == "actual" and a.flow_id}
                    if scenario.use_actuals else set())

    for year in range(assumptions.start_year, assumptions.end_year + 1):
        year_warnings = list(scenario_warnings) if year == assumptions.start_year else []
        result = _simulate_year(
            year, scope_assets, scope_household_cashflows, flows_by_asset,
            scope_liabilities, balances, scenario, assumptions, year_warnings,
            growth_rate_fn, tax_state, working_portfolio.people, actual_slots,
        )
        result.ages = {p.name: p.age_in(year) for p in working_portfolio.people}
        annual_results.append(result)

    return SimulationResult(scenario_id=scenario.scenario_id, annual_results=annual_results)


def _simulate_year(
    year: int,
    assets: List[Asset],
    household_cashflows: list,
    flows_by_asset: dict,
    liabilities: List[Liability],
    balances: dict,
    scenario: Scenario,
    assumptions: Assumptions,
    warnings: List[str],
    growth_rate_fn: Optional[GrowthRateFn] = None,
    tax_state: Optional[dict] = None,
    people: Optional[list] = None,
    actual_slots: Optional[set] = None,
) -> AnnualHouseholdResult:
    asset_results: List[AnnualAssetResult] = []
    total_asset_income = 0.0
    total_asset_expense = 0.0

    inflation_rate = assumptions.inflation_rate

    for asset in assets:
        growth_rate = growth_rate_fn(asset, year) if growth_rate_fn else asset.annual_growth_rate
        asset_result = _simulate_asset_year(
            asset, flows_by_asset.get(asset.asset_id, []), year, inflation_rate, growth_rate
        )
        asset_results.append(asset_result)
        total_asset_income += asset_result.gross_income
        total_asset_expense += asset_result.gross_expense

    sold_asset_ids = {r.asset_id for r in asset_results if r.sold_this_year}
    sale_months = {a.asset_id: a.sale_month for a in assets if a.asset_id in sold_asset_ids}
    liability_results: List[AnnualLiabilityResult] = [
        _simulate_liability_year(li, year, balances, sold_asset_ids, sale_months) for li in liabilities
    ]
    total_debt_service = sum(r.debt_service for r in liability_results)
    total_interest_paid = sum(r.interest_paid for r in liability_results)
    total_liabilities = sum(r.ending_balance for r in liability_results)

    tax_context = tax_engine.TaxContext(
        assets=assets, flows_by_asset=flows_by_asset, household_flows=household_cashflows,
        liability_results=liability_results, liabilities={li.liability_id: li for li in liabilities},
        people=people or [], sold_asset_ids=sold_asset_ids,
        carryforward=tax_state if tax_state is not None else {},
    )
    # Social Security earnings test (per person) -- before income is totalled and taxed.
    if people:
        earned = tax_engine.earned_income_by_person(year, inflation_rate, tax_context)
        warnings.extend(social_security.apply_earnings_test(
            year, inflation_rate, household_cashflows, people, earned, actual_slots or set()))

    household_income = 0.0
    household_expense = 0.0
    for cf in household_cashflows:
        signed = cf.signed_amount_in_year(year, inflation_rate=inflation_rate)
        if cf.direction == CashflowDirection.INFLOW:
            household_income += signed
        elif cf.direction == CashflowDirection.OUTFLOW:
            household_expense += -signed  # store as positive expense magnitude

    total_gross_income = total_asset_income + household_income
    total_expenses = total_asset_expense + household_expense + total_debt_service

    this_year_cashflows = [
        cf for cf in (
            household_cashflows
            + [cf for a in assets for cf in flows_by_asset.get(a.asset_id, [])]
        )
        if _flow_active_in_year(cf, year)
    ]
    tax_estimate = tax_engine.estimate_taxes_for_year(year, this_year_cashflows, assumptions, tax_context)

    net_cashflow = total_gross_income - total_expenses - tax_estimate.total_estimated_tax
    surplus_or_deficit = net_cashflow

    withdrawal_breakdown: dict[str, float] = {}
    withdrawal_amount = 0.0
    reinvestment_amount = 0.0

    if surplus_or_deficit < 0:
        shortfall = -surplus_or_deficit
        withdrawal_breakdown = withdrawal_engine.withdraw_for_shortfall(
            shortfall, assets, scenario
        )
        withdrawal_amount = sum(withdrawal_breakdown.values())
        if withdrawal_amount + 1e-6 < shortfall:
            warnings.append(
                f"Year {year}: shortfall of ${shortfall:,.0f} could not be fully covered "
                f"by available liquid/semi-liquid assets (covered ${withdrawal_amount:,.0f}). "
                "Consider a scenario that allows illiquid withdrawals or an asset sale."
            )
    elif surplus_or_deficit > 0:
        reinvest_breakdown = withdrawal_engine.reinvest_surplus(
            surplus_or_deficit, assets, scenario
        )
        reinvestment_amount = sum(reinvest_breakdown.values())

    liquid_categories = {"liquid", "semi_liquid"}
    liquid_net_worth = sum(a.current_value for a in assets if a.active and a.liquidity_category.value in liquid_categories)
    illiquid_net_worth = sum(a.current_value for a in assets if a.active and a.liquidity_category.value not in liquid_categories)
    total_assets = liquid_net_worth + illiquid_net_worth
    total_net_worth = total_assets - total_liabilities

    return AnnualHouseholdResult(
        year=year,
        total_gross_income=total_gross_income,
        total_expenses=total_expenses,
        total_estimated_tax=tax_estimate.total_estimated_tax,
        net_cashflow=net_cashflow,
        surplus_or_deficit=surplus_or_deficit,
        withdrawal_amount=withdrawal_amount,
        withdrawal_breakdown=withdrawal_breakdown,
        reinvestment_amount=reinvestment_amount,
        total_net_worth=total_net_worth,
        liquid_net_worth=liquid_net_worth,
        illiquid_net_worth=illiquid_net_worth,
        total_assets=total_assets,
        total_liabilities=total_liabilities,
        total_debt_service=total_debt_service,
        total_interest_paid=total_interest_paid,
        asset_results=asset_results,
        liability_results=liability_results,
        tax_estimate=tax_estimate,
        warnings=warnings,
    )


def _simulate_asset_year(
    asset: Asset, asset_flows: list, year: int, inflation_rate: float = 0.0,
    growth_rate: Optional[float] = None,
) -> AnnualAssetResult:
    """Advance a single asset by one year: apply growth, then apply any
    cashflows flagged affects_asset_value, and report gross income/expense
    for that asset in the year (regardless of whether they affect value --
    e.g. rent income affects cash but typically not the property's value).

    If asset is inactive, or the year is before its start_year, or after a
    configured sale_year, the asset is treated as flat (no growth, no
    cashflows) and reported with zero activity.
    """
    beginning_value = asset.current_value

    if not asset.active or year < asset.start_year:
        asset.current_value = beginning_value
        return AnnualAssetResult(
            asset_id=asset.asset_id,
            year=year,
            beginning_value=beginning_value,
            growth_amount=0.0,
            net_cashflow_affecting_value=0.0,
            ending_value=beginning_value,
        )

    if asset.sale_year is not None and year > asset.sale_year:
        # Asset was already sold in a prior year; nothing left to simulate.
        asset.current_value = 0.0
        return AnnualAssetResult(
            asset_id=asset.asset_id,
            year=year,
            beginning_value=0.0,
            growth_amount=0.0,
            net_cashflow_affecting_value=0.0,
            ending_value=0.0,
        )

    if growth_rate is None:
        growth_rate = asset.annual_growth_rate
    growth_amount = beginning_value * growth_rate
    value_after_growth = beginning_value + growth_amount

    gross_income = 0.0
    gross_expense = 0.0
    net_cashflow_affecting_value = 0.0

    for cf in asset_flows:
        if not _flow_active_in_year(cf, year):
            continue
        signed = cf.signed_amount_in_year(year, inflation_rate=inflation_rate)

        if cf.direction == CashflowDirection.INFLOW:
            gross_income += signed
        elif cf.direction == CashflowDirection.OUTFLOW:
            gross_expense += -signed

        if cf.affects_asset_value:
            net_cashflow_affecting_value += signed

    ending_value = value_after_growth + net_cashflow_affecting_value

    sold_this_year = asset.sale_year == year
    sale_proceeds = 0.0
    if sold_this_year:
        # Sale proceeds, if modeled, should come through a sale_proceeds
        # cashflow on the asset. That flow is a household-cash inflow (it
        # already contributed to gross_income above since it's an INFLOW
        # cashflow); the MVP convention is simply that the asset's value
        # goes to zero in its sale_year regardless of the growth/flow math
        # above, since the underlying asset no longer exists.
        sale_proceeds = sum(
            cf.signed_amount_in_year(year, inflation_rate=inflation_rate)
            for cf in asset_flows
            if cf.flow_type.value == "sale_proceeds"
        )
        ending_value = 0.0

    # Persist the new value back onto the asset so next year's
    # beginning_value reflects this year's activity. This is the one place
    # in the simulation where an Asset is mutated in place; the working
    # Portfolio this operates on is always a scenario-scoped deep copy
    # (see scenarios.apply_scenario), so the caller's original data is safe.
    asset.current_value = ending_value

    return AnnualAssetResult(
        asset_id=asset.asset_id,
        year=year,
        beginning_value=beginning_value,
        growth_amount=growth_amount,
        net_cashflow_affecting_value=net_cashflow_affecting_value,
        ending_value=ending_value,
        gross_income=gross_income,
        gross_expense=gross_expense,
        sold_this_year=sold_this_year,
        sale_proceeds=sale_proceeds,
    )


def _simulate_liability_year(
    liability: Liability, year: int, balances: dict, sold_asset_ids: set, sale_months: Optional[dict] = None
) -> AnnualLiabilityResult:
    """Advance one debt by one year and update balances[liability_id].

    - Before its start_year (or if inactive) the debt doesn't exist yet: $0.
    - Otherwise the subclass's year_activity() decides interest and
      principal (Loan amortizes; CreditLine pays interest only).
    - If the asset securing it was sold this year, the remaining balance is
      paid off from the sale.
    """
    lid = liability.liability_id
    if not liability.active or year < liability.start_year:
        return AnnualLiabilityResult(liability_id=lid, year=year)

    beginning = balances.get(lid, 0.0)
    if beginning <= 0:
        return AnnualLiabilityResult(liability_id=lid, year=year)

    sold = liability.secured_by_asset_id in sold_asset_ids
    payoff = liability.payoff_year is not None and year >= liability.payoff_year
    # Paid off part-way through the year: only the months before the payoff
    # month carry interest and payments.
    months = 12
    if sold and (sale_months or {}).get(liability.secured_by_asset_id):
        months = sale_months[liability.secured_by_asset_id] - 1
    if payoff and year == liability.payoff_year and liability.payoff_month:
        months = min(months, liability.payoff_month - 1)
    interest, principal, ending = liability.year_activity(beginning, months)

    if (sold or payoff) and ending > 0:
        principal += ending  # pay off the rest: asset sold, or scheduled payoff/refinance
        ending = 0.0

    balances[lid] = ending
    return AnnualLiabilityResult(
        liability_id=lid,
        year=year,
        beginning_balance=beginning,
        interest_paid=interest,
        principal_paid=principal,
        ending_balance=ending,
        paid_off_this_year=ending <= 0.005,
    )


def _flow_active_in_year(cf, year: int) -> bool:
    if year in cf.year_overrides:  # a recorded actual/forecast for this year
        return True
    if year < cf.start_year:
        return False
    if cf.end_year is not None and year > cf.end_year:
        return False
    if cf.frequency.value == "one_time" and year != cf.start_year:
        return False
    return True


def run_all_scenarios(portfolio: Portfolio) -> List[SimulationResult]:
    """Convenience helper: run every scenario stored in the portfolio and
    return one SimulationResult per scenario, in the same order they appear
    in portfolio.scenarios. If the portfolio has no scenarios defined, runs
    a single implicit Base Case with no overrides.
    """
    scenario_list = portfolio.scenarios or [scenario_engine.make_base_case_scenario()]
    results = []
    for scenario in scenario_list:
        results.append(run_simulation(portfolio, scenario))
        # NOTE: each run_simulation call re-applies the scenario to a fresh
        # deep copy of the *original* portfolio, so asset current_value
        # mutations from one scenario's withdrawal/reinvestment logic never
        # leak into the next scenario's run.
    return results
