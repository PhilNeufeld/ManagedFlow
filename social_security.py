"""
social_security.py

The Social Security retirement EARNINGS TEST: if you claim before full
retirement age (FRA) and keep earning, SSA withholds benefits.

- Years before the year you reach FRA: $1 withheld for every $2 of earnings
  over the lower limit ($24,480 in 2026; $25,560 in 2027).
- The year you reach FRA: $1 for every $3 over the higher limit ($65,160 in
  2026; $67,920 in 2027), counting only earnings and months BEFORE FRA.
- From the FRA month on: no test.
- First year of benefits ("grace year"): any month you're retired -- wages
  under 1/12 of the limit AND no substantial services in your own business --
  is paid in full whatever you earn for the year.

Earnings = wages + net earnings from self-employment (92.35% of net profit).
Pensions, Social Security, rent, interest, distributions and gains don't count.

Withheld benefits aren't lost: at FRA, SSA raises the benefit to credit the
months withheld. That later increase is NOT modeled (a conservative choice).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import tax_tables as T
from models import CashflowDirection, FlowType, Person


@dataclass
class EarningsTestResult:
    withheld: float
    earnings: float
    limit: float
    months_tested: int
    note: str = ""


def monthly_wages(year: int, inflation: float, person_id: str, flows, default_person: str) -> List[float]:
    """The person's wages in each month of the year (index 0 = January)."""
    months = [0.0] * 12
    for cf in flows:
        if cf.flow_type != FlowType.SALARY_INCOME or cf.direction != CashflowDirection.INFLOW:
            continue
        if (cf.person_id or default_person) != person_id:
            continue
        active = [m for m in range(1, 13) if cf.month_is_active(year, m)]
        amount = cf.annual_amount_in_year(year, inflation)
        if not active:   # e.g. a recorded actual for a year the plan doesn't cover: spread it
            active = list(range(1, 13))
        for m in active:
            months[m - 1] += amount / len(active)
    return months


def earnings_test(person: Person, year: int, inflation: float, benefit_months: List[int], benefit: float,
                  earnings: float, wages_by_month: List[float], has_self_employment: bool,
                  first_benefit_year: int) -> EarningsTestResult:
    """How much of `benefit` (the year's total, paid over `benefit_months`) is withheld."""
    fra_year, fra_month = person.full_retirement_date()
    if not benefit_months or benefit <= 0 or year > fra_year:
        return EarningsTestResult(0.0, earnings, 0.0, 0)
    per_month = benefit / len(benefit_months)
    lower, upper = T.earnings_test_limits(year, inflation)
    if year == fra_year:
        tested = [m for m in benefit_months if m < fra_month]
        counted_earnings = earnings * (fra_month - 1) / 12   # earnings before the FRA month (spread evenly)
        limit, ratio = upper, 3
    else:
        tested = list(benefit_months)
        counted_earnings, limit, ratio = earnings, lower, 2
    note = ""
    if year == first_benefit_year:
        busy = has_self_employment and person.substantial_self_employment
        retired = [m for m in tested if wages_by_month[m - 1] <= limit / 12 and not busy]
        tested = [m for m in tested if m not in retired]
        if retired:
            note = f"grace year: {len(retired)} retired month(s) paid in full"
        elif busy:
            note = ("grace year: no month counts as retired because of self-employment "
                    "(substantial services assumed)")
    excess = max(0.0, counted_earnings - limit) / ratio
    withheld = min(excess, per_month * len(tested))
    return EarningsTestResult(withheld, counted_earnings, limit, len(tested), note)


def apply_earnings_test(year: int, inflation: float, household_flows, people: List[Person],
                        earned: Dict[str, Dict[str, float]], actual_slots: set) -> List[str]:
    """On a scenario's WORKING copy: reduce each person's Social Security benefit
    for the year by the earnings test (sets cf.year_overrides[year]). Benefits
    recorded as an ACTUAL for the year are left alone (they're what was paid).
    Returns warning lines."""
    if not people:
        return []
    by_id = {p.person_id: p for p in people}
    default = people[0].person_id
    lines = []
    for cf in household_flows:
        if cf.flow_type != FlowType.SOCIAL_SECURITY_INCOME or cf.direction != CashflowDirection.INFLOW:
            continue
        person = by_id.get(cf.person_id or default)
        if person is None or (cf.flow_id, year) in actual_slots:
            continue
        benefit = cf.annual_amount_in_year(year, inflation)
        months = [m for m in range(1, 13) if cf.month_is_active(year, m)]
        if year in cf.year_overrides and not months:
            months = list(range(1, 13))
        e = earned.get(person.person_id, {"wages": 0.0, "se": 0.0})
        nese = max(0.0, e["se"]) * T.SE_EARNINGS_FACTOR
        result = earnings_test(
            person, year, inflation, months, benefit, e["wages"] + nese,
            monthly_wages(year, inflation, person.person_id, household_flows, default),
            has_self_employment=nese > 0, first_benefit_year=cf.start_year)
        if result.withheld > 0.005:
            cf.year_overrides[year] = benefit - result.withheld
            lines.append(
                f"Social Security earnings test: ${result.withheld:,.0f} of {person.name}'s "
                f"${benefit:,.0f} benefit withheld (earnings ${result.earnings:,.0f} vs the "
                f"${result.limit:,.0f} limit{'; ' + result.note if result.note else ''}). SSA credits "
                "withheld months back as a higher benefit at full retirement age -- not modeled.")
    return lines
