"""
spending.py

Two questions about the household's LIVING EXPENSES (cashflows of type living_expense):

1. Spending policy (a scenario setting) -- spending that adjusts to markets.
   "fixed": spend the plan every year. "guardrails": a Guyton-Klinger-style rule:
     - Each year look at last year's DRAW RATE: net withdrawals from liquid savings
       (withdrawals - reinvested surplus) / liquid assets at the end of the year.
     - Draw rate above the upper guardrail -> cut spending by the step (e.g. 10%).
     - Draw rate below the lower guardrail -> raise it by the step, up to the ceiling
       (a ceiling of 100% only restores earlier cuts).
     - After a year in which liquid assets lost money, skip that year's inflation raise.
     - Never below the floor (e.g. 80% of the plan) or above the ceiling.
   Years with recorded actuals are what really happened, so they're never adjusted.
   run_simulation() calls guardrail_step() each year; Monte Carlo then shows how spending
   would flex across good and bad markets.

2. Highest steady spending -- the largest inflation-adjusted living-expense level the plan
   can sustain for life: no year with a shortfall the liquid assets can't cover, and
   (optionally) liquid assets never below a reserve in today's dollars. Solved by bisection
   on deterministic runs, or (Monte Carlo) the highest level that succeeds in a target share
   of random-market runs.
"""
from __future__ import annotations

import copy
import dataclasses
import math
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from models import CashflowDirection, FlowType, LiquidityCategory

LIQUID = {LiquidityCategory.LIQUID.value, LiquidityCategory.SEMI_LIQUID.value}


def living_flows(cashflows) -> list:
    """The cashflows a spending policy adjusts: household living-expense outflows."""
    return [cf for cf in cashflows if cf.asset_id is None and cf.direction == CashflowDirection.OUTFLOW
            and cf.flow_type == FlowType.LIVING_EXPENSE]


def draw_rate(prev) -> float:
    """Last year's net draw on liquid savings as a share of liquid assets at year end."""
    net = prev.withdrawal_amount - prev.reinvestment_amount
    liquid = prev.liquid_net_worth
    if liquid <= 0:
        return float("inf") if net > 0 else 0.0
    return net / liquid


def liquid_return(prev, assets_by_id) -> float:
    """Last year's growth of liquid assets as a share of their starting value."""
    begin = growth = 0.0
    for ar in prev.asset_results:
        a = assets_by_id.get(ar.asset_id)
        if a is not None and getattr(a.liquidity_category, "value", a.liquidity_category) in LIQUID:
            begin += ar.beginning_value
            growth += ar.growth_amount
    return growth / begin if begin > 0 else 0.0


def guardrail_step(scenario, multiplier: float, prev, assets_by_id, inflation: float) -> Tuple[float, str]:
    """Next year's spending multiplier (x the plan) and a note on what changed."""
    if getattr(scenario, "spending_policy", "fixed") != "guardrails" or prev is None:
        return multiplier, ""
    notes = []
    rate = draw_rate(prev)
    step, floor, ceiling = scenario.guardrail_step, scenario.spending_floor, scenario.spending_ceiling
    if rate > scenario.guardrail_upper and multiplier > floor:
        multiplier = max(floor, multiplier * (1 - step))
        notes.append(f"cut {step:.0%} (draw rate {rate:.1%} above {scenario.guardrail_upper:.1%})")
    elif rate < scenario.guardrail_lower and multiplier < ceiling:
        multiplier = min(ceiling, multiplier * (1 + step))
        notes.append(f"raised {step:.0%} (draw rate {rate:.1%} below {scenario.guardrail_lower:.1%})")
    if scenario.skip_inflation_after_loss and inflation > 0 and multiplier > floor \
            and liquid_return(prev, assets_by_id) < 0:
        multiplier = max(floor, multiplier / (1 + inflation))
        notes.append("no inflation raise after a losing year")
    return multiplier, "; ".join(notes)


# ---------------------------------------------------------------------------------------------
# Highest steady spending
# ---------------------------------------------------------------------------------------------

@dataclass
class SteadySpendingResult:
    amount: float                       # living expenses in the first year (today's dollars), sustainable
    planned: float                      # what the plan has now
    success_rate: Optional[float]       # Monte Carlo only: share of runs that succeed at `amount`
    binding_year: Optional[int]         # the year that fails first just above `amount`
    reason: str                         # why spending can't go higher
    min_liquid_today: float
    years: List[int] = field(default_factory=list)
    liquid_path: List[float] = field(default_factory=list)   # liquid assets each year at `amount` (deterministic)

    @property
    def headroom(self) -> float:
        return self.amount - self.planned


def _scaled(portfolio, factor: float):
    p = copy.deepcopy(portfolio)
    for cf in living_flows(p.cashflows):
        cf.amount *= factor
        cf.year_overrides = {}           # the solver asks what you COULD spend: plan amounts, not actuals
    return p


def _fails(results, min_liquid_today: float, inflation: float, start: int) -> Tuple[Optional[int], str]:
    """First failing year and why, or (None, '')."""
    for r in results:
        shortfall = -r.surplus_or_deficit
        if shortfall > 0 and r.withdrawal_amount + 1e-6 < shortfall:
            return r.year, "a shortfall the liquid assets can't cover"
        if min_liquid_today and r.liquid_net_worth < min_liquid_today * (1 + inflation) ** (r.year - start):
            return r.year, f"liquid assets below the ${min_liquid_today:,.0f} reserve (today's dollars)"
    return None, ""


def highest_steady_spending(portfolio, scenario, min_liquid_today: float = 0.0, *, tolerance: float = 100.0,
                            monte_carlo_runs: int = 0, target_success: float = 0.9, seed: Optional[int] = 42,
                            progress: Optional[Callable[[float], None]] = None) -> SteadySpendingResult:
    """The largest first-year living-expense amount (growing as the plan's living expenses do)
    the plan can sustain through its last year. monte_carlo_runs > 0: the largest amount that
    succeeds in at least target_success of that many random-market runs (same seed each step)."""
    import montecarlo
    import simulation

    flows = living_flows(portfolio.cashflows)
    if not flows:
        raise ValueError("The plan has no living-expense cashflow to solve for (type living_expense).")
    start = portfolio.assumptions.start_year
    planned = sum(cf.annual_amount_in_year(start) if cf.start_year <= start else cf.amount for cf in flows)
    base = sum(cf.amount for cf in flows) or 1.0
    fixed = dataclasses.replace(scenario, spending_policy="fixed")
    inflation = (scenario.inflation_rate_override if scenario.inflation_rate_override is not None
                 else portfolio.assumptions.inflation_rate)

    def evaluate(amount: float):
        p = _scaled(portfolio, amount / base)
        if monte_carlo_runs:
            mc = montecarlo.run_monte_carlo(p, fixed, n_runs=monte_carlo_runs, seed=seed)
            ok = sum(1 for y in mc.first_failure_year if y is None) / monte_carlo_runs
            first = min((y for y in mc.first_failure_year if y is not None), default=None)
            return ok >= target_success, ok, first, "too many market runs fall short"
        results = simulation.run_simulation(p, fixed).annual_results
        year, why = _fails(results, min_liquid_today, inflation, start)
        return year is None, None, year, why

    steps = [0]

    def tick():
        steps[0] += 1
        if progress:
            progress(min(0.99, steps[0] / 30))

    lo, hi = 0.0, max(planned, 10_000.0)
    ok, rate_lo, _, _ = evaluate(lo); tick()
    if not ok:
        return SteadySpendingResult(0.0, planned, rate_lo, start, "the plan falls short even with no living expenses",
                                    min_liquid_today)
    # Grow the upper bound until it fails (cap at $50M a year).
    while True:
        ok, rate, year, why = evaluate(hi); tick()
        if not ok:
            fail_year, fail_why = year, why
            break
        lo, rate_lo = hi, rate
        hi *= 2
        if hi > 50_000_000:
            return SteadySpendingResult(lo, planned, rate_lo, None, "no limit found below $50M a year", min_liquid_today)
    while hi - lo > tolerance:
        mid = (lo + hi) / 2
        ok, rate, year, why = evaluate(mid); tick()
        if ok:
            lo, rate_lo = mid, rate
        else:
            hi, fail_year, fail_why = mid, year, why
    # Finish on a round $100: the next $100 up if it still works, else the $100 below (never overstate).
    up = math.ceil(lo / 100) * 100
    if up < hi:
        ok, rate, _, _ = evaluate(up); tick()
        if ok:
            lo, rate_lo = up, rate
    if lo % 100:
        lo = math.floor(lo / 100) * 100
    years, path = [], []
    if not monte_carlo_runs:
        res = simulation.run_simulation(_scaled(portfolio, lo / base), fixed).annual_results
        years, path = [r.year for r in res], [r.liquid_net_worth for r in res]
    if progress:
        progress(1.0)
    return SteadySpendingResult(lo, planned, rate_lo, fail_year, fail_why, min_liquid_today, years, path)
