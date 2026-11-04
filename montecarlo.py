"""
montecarlo.py

Monte Carlo simulation: run the same plan many times, each time with
randomly drawn market years, to see the RANGE of outcomes instead of one
straight line.

What varies in each run
-----------------------
- Each asset's growth rate, every year:  your rate + a random swing.
  The swing is drawn from a normal (bell-curve) distribution whose width
  is the asset type's volatility. With equities at 15%, about two years in
  three land within +/-15 points of your rate; about one year in twenty
  lands more than 30 points away.
- Inflation: one long-run rate per run, drawn around your assumption.

What stays fixed
----------------
Your cashflows, loans, events, taxes and withdrawal rules.

What you get
------------
- success_rate: share of runs where every year's shortfall could be
  covered from liquid/semi-liquid assets (you never "ran out of money").
- Percentile bands: in the 10th-percentile line, 9 runs in 10 did better.
  The 50th is the median. The 90th is a good-luck outcome.

Simplifications (be honest about these when reading results)
-----------------------------------------------------------
- Asset swings are independent of each other. Real markets move together,
  so true bad years are worse than these, and the 10th percentile here is
  probably optimistic.
- Normal distributions understate rare crashes ("fat tails").
- Default volatilities are rough, round numbers -- change them.
- Even with zero drift, randomness lowers the median: losing 20% then
  gaining 20% leaves you at 96%, not 100%. That's real, not a bug.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import numpy as np

import simulation
from models import AssetType, Portfolio, Scenario

# Standard deviation of each asset type's annual growth rate (0.15 = 15 points).
DEFAULT_VOLATILITY: Dict[str, float] = {
    AssetType.EQUITY_FUND.value: 0.15,
    AssetType.REAL_ESTATE.value: 0.08,
    AssetType.BUSINESS.value: 0.12,
    AssetType.TRUST.value: 0.10,
    AssetType.OTHER.value: 0.08,
    AssetType.ANNUITY.value: 0.0,
    AssetType.PENSION.value: 0.0,
    AssetType.CASH.value: 0.0,
}
DEFAULT_INFLATION_VOLATILITY = 0.01
MIN_GROWTH_RATE = -0.95  # an asset can't lose more than 95% in one year


@dataclass
class MonteCarloResult:
    scenario_id: str
    n_runs: int
    seed: Optional[int]
    years: List[int]
    net_worth: np.ndarray          # shape (n_runs, n_years)
    liquid_net_worth: np.ndarray   # shape (n_runs, n_years)
    first_failure_year: List[Optional[int]]  # per run; None = never ran short
    inflation_rates: np.ndarray    # the inflation rate drawn for each run

    @property
    def success_rate(self) -> float:
        return sum(y is None for y in self.first_failure_year) / self.n_runs

    def percentiles(self, which: str = "net_worth", ps=(10, 50, 90)) -> Dict[int, List[float]]:
        data = getattr(self, which)
        return {p: np.percentile(data, p, axis=0).tolist() for p in ps}

    def failure_year_counts(self) -> Dict[int, int]:
        counts: Dict[int, int] = {}
        for y in self.first_failure_year:
            if y is not None:
                counts[y] = counts.get(y, 0) + 1
        return dict(sorted(counts.items()))

    def summary_rows(self) -> List[dict]:
        nw = self.percentiles("net_worth")
        liquid = self.percentiles("liquid_net_worth")
        return [
            {
                "Year": year,
                "Net worth, 10th pct": nw[10][i],
                "Net worth, median": nw[50][i],
                "Net worth, 90th pct": nw[90][i],
                "Liquid, 10th pct": liquid[10][i],
                "Liquid, median": liquid[50][i],
            }
            for i, year in enumerate(self.years)
        ]


def _ran_short(year_result) -> bool:
    shortfall = -year_result.surplus_or_deficit
    return shortfall > 0 and year_result.withdrawal_amount + 1e-6 < shortfall


def run_monte_carlo(
    portfolio: Portfolio,
    scenario: Scenario,
    n_runs: int = 1000,
    seed: Optional[int] = None,
    volatility: Optional[Dict[str, float]] = None,
    inflation_volatility: float = DEFAULT_INFLATION_VOLATILITY,
    progress: Optional[Callable[[int, int], None]] = None,
) -> MonteCarloResult:
    """Run `scenario` n_runs times with random growth and inflation.

    seed: give a number to get the exact same results every time (useful
    for comparing plans and for tests); None = different every time.
    volatility: overrides for DEFAULT_VOLATILITY, keyed by asset type
    value, e.g. {"equity_fund": 0.18}.
    progress: optional callback(done, total) for a progress bar.
    """
    if n_runs < 1:
        raise ValueError("n_runs must be at least 1")

    vol = {**DEFAULT_VOLATILITY, **(volatility or {})}
    start, end = portfolio.assumptions.start_year, portfolio.assumptions.end_year
    years = list(range(start, end + 1))
    asset_index = {a.asset_id: i for i, a in enumerate(portfolio.assets)}
    asset_vols = np.array([vol.get(a.asset_type.value, 0.0) for a in portfolio.assets])
    base_inflation = (
        scenario.inflation_rate_override
        if scenario.inflation_rate_override is not None
        else portfolio.assumptions.inflation_rate
    )

    rng = np.random.default_rng(seed)
    net_worth = np.zeros((n_runs, len(years)))
    liquid = np.zeros((n_runs, len(years)))
    inflation_rates = np.zeros(n_runs)
    first_failure: List[Optional[int]] = []

    for run in range(n_runs):
        # One random swing per asset per year, scaled by that asset's volatility.
        swings = rng.standard_normal((len(asset_index), len(years))) * asset_vols[:, None]
        inflation = base_inflation + rng.standard_normal() * inflation_volatility
        inflation_rates[run] = inflation

        def growth_rate(asset, year, swings=swings):
            i = asset_index.get(asset.asset_id)
            if i is None:
                return asset.annual_growth_rate
            return max(MIN_GROWTH_RATE, asset.annual_growth_rate + swings[i, year - start])

        run_scenario = dataclasses.replace(scenario, inflation_rate_override=inflation)
        result = simulation.run_simulation(portfolio, run_scenario, growth_rate_fn=growth_rate)

        net_worth[run] = [r.total_net_worth for r in result.annual_results]
        liquid[run] = [r.liquid_net_worth for r in result.annual_results]
        first_failure.append(next((r.year for r in result.annual_results if _ran_short(r)), None))
        if progress:
            progress(run + 1, n_runs)

    return MonteCarloResult(
        scenario_id=scenario.scenario_id, n_runs=n_runs, seed=seed, years=years,
        net_worth=net_worth, liquid_net_worth=liquid,
        first_failure_year=first_failure, inflation_rates=inflation_rates,
    )
