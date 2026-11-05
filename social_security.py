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
import re

from models import Cashflow, CashflowDirection, FlowType, Person


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
        info = getattr(cf, "ss_info", None)
        if info is not None:
            months = info["months_by_year"].get(year, [])
            # At full retirement age SSA credits back the months the earnings test withheld:
            # the benefit is recomputed as if claimed that many months later.
            withheld_months = getattr(cf, "ss_withheld_months", 0)
            fra = info["fra"]
            if withheld_months and months and year >= fra[0]:
                after = [m for m in months if (year, m) >= fra]
                ratio = _recredit_ratio(info, withheld_months)
                benefit = benefit * (1 + (ratio - 1) * len(after) / len(months))
                cf.year_overrides[year] = benefit
                if year == fra[0]:
                    lines.append(f"Social Security: {person.name}'s benefit recomputed at full retirement age "
                                 f"to credit back {withheld_months} withheld month(s) (+{ratio - 1:.1%}).")
        else:
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
            per_month = benefit / len(months) if months else 0
            if per_month:
                import math
                cf.ss_withheld_months = getattr(cf, "ss_withheld_months", 0) + min(
                    result.months_tested, math.ceil(result.withheld / per_month - 1e-9))
            lines.append(
                f"Social Security earnings test: ${result.withheld:,.0f} of {person.name}'s "
                f"${benefit:,.0f} benefit withheld (earnings ${result.earnings:,.0f} vs the "
                f"${result.limit:,.0f} limit{'; ' + result.note if result.note else ''}). SSA credits "
                "withheld months back as a higher benefit at full retirement age -- not modeled.")
    return lines


# =====================================================================================
# Benefits from SSA's rules (SocialSecurity records -> cashflows on a working copy)
# =====================================================================================

def _ym(total: int) -> tuple:
    return total // 12, total % 12 + 1


def _months(a: tuple, b: tuple) -> int:
    """Months from date a to date b ((year, month) pairs)."""
    return (b[0] - a[0]) * 12 + (b[1] - a[1])


def claim_date(person: Person, years: int, months: int = 0) -> tuple:
    """(year, month) benefits start when claiming at an age. At exactly 62 the first
    month is the one after the birthday month (you must be 62 all month)."""
    total = (person.birth_year + years) * 12 + (person.birth_month - 1) + months
    if (years, months) == (62, 0):
        total += 1
    return _ym(total)


def own_factor(months_early: int, months_fra_to_70: int = 36) -> float:
    """Own benefit vs. PIA. Early: 5/9 of 1% a month for 36 months, 5/12 of 1% beyond.
    Late: 2/3 of 1% a month (8%/yr), only up to age 70."""
    if months_early > 0:
        return 1 - (min(months_early, 36) * 5 / 900 + max(months_early - 36, 0) * 5 / 1200)
    return 1 + min(-months_early, months_fra_to_70) * 2 / 300


def spousal_factor(months_early: int) -> float:
    """Spousal benefit vs. its full amount: 25/36 of 1% a month for 36 months, 5/12 of 1%
    beyond. No increase for waiting past full retirement age."""
    if months_early <= 0:
        return 1.0
    return 1 - (min(months_early, 36) * 25 / 3600 + max(months_early - 36, 0) * 5 / 1200)


def death_date(person: Person) -> tuple:
    """Planning assumption: the birthday month at the person's plan-to age."""
    return person.birth_year + person.life_expectancy, person.birth_month


@dataclass
class BenefitPlan:
    """How one record's benefit works out (for the screens and the claiming analysis)."""
    person_id: object
    claim: tuple
    fra: tuple
    months_early: int
    own_factor: float
    own_monthly: float             # plan-start-year dollars
    spousal_start: Optional[tuple] = None
    spousal_monthly: float = 0.0
    survivor_start: Optional[tuple] = None
    survivor_monthly: float = 0.0  # top-up over own + spousal, plan-start dollars


def plan_benefits(p) -> Dict[object, BenefitPlan]:
    people = {x.person_id: x for x in p.people}
    recs = {s.person_id: s for s in p.social_security if s.person_id in people}
    plans = {}
    for pid, s in recs.items():
        person = people[pid]
        fra = person.full_retirement_date()
        claim = claim_date(person, s.claim_age_years, s.claim_age_months)
        me = _months(claim, fra)
        to70 = _months(fra, (person.birth_year + 70, person.birth_month))
        f = own_factor(me, to70)
        plans[pid] = BenefitPlan(pid, claim, fra, me, f, s.pia * f)
    for pid, bp in plans.items():
        s = recs[pid]
        others = [o for o in plans if o != pid]
        if not others:
            continue
        spouse_id = others[0]
        sp, sp_rec = plans[spouse_id], recs[spouse_id]
        excess = 0.5 * sp_rec.pia - s.pia
        if s.spousal and excess > 0:
            start = max(bp.claim, sp.claim)              # needs both to have filed (deemed filing)
            bp.spousal_start = start
            bp.spousal_monthly = excess * spousal_factor(_months(start, bp.fra))
        if s.survivor:
            deceased = max(sp.own_monthly, 0.825 * sp_rec.pia) if sp.months_early > 0 else sp.own_monthly
            top_up = deceased - bp.own_monthly
            if top_up > 0:
                bp.survivor_start = death_date(people[spouse_id])
                bp.survivor_monthly = top_up
    return plans


def apply_benefits(p) -> None:
    """On a scenario's WORKING copy: one cashflow per benefit (own, spousal, survivor)
    with each year's amount: monthly benefit x COLA x months paid. Benefits stop at the
    person's death (plan-to age); spousal stops at the spouse's death, when the
    survivor top-up starts. Own and spousal carry what the earnings test needs to
    credit withheld months back at full retirement age."""
    if not p.social_security:
        return
    a0, a1 = p.assumptions.start_year, p.assumptions.end_year
    people = {x.person_id: x for x in p.people}
    recs = {s.person_id: s for s in p.social_security}
    plans = plan_benefits(p)
    for pid, bp in plans.items():
        s, person = recs[pid], people[pid]
        my_death = death_date(person)
        spouse = next((people[o] for o in plans if o != pid), None)
        spouse_death = death_date(spouse) if spouse else (9999, 1)
        parts = [("own", bp.own_monthly, bp.claim, my_death),
                 ("spousal", bp.spousal_monthly, bp.spousal_start, min(my_death, spouse_death)),
                 ("survivor", bp.survivor_monthly, bp.survivor_start, my_death)]
        for kind, monthly, start, stop in parts:
            if not monthly or start is None:
                continue
            overrides, months_by_year = {}, {}
            for year in range(a0, a1 + 1):
                paid = [m for m in range(1, 13) if start <= (year, m) < stop]
                if paid:
                    overrides[year] = monthly * (1 + s.cola) ** (year - a0) * len(paid)
                    months_by_year[year] = paid
            if not overrides:
                continue
            cf = Cashflow(flow_id=f"ss:{s.ss_id}:{kind}", name=f"{person.name} Social Security ({kind})",
                          flow_type=FlowType.SOCIAL_SECURITY_INCOME, direction=CashflowDirection.INFLOW, amount=0.0,
                          start_year=start[0], start_month=start[1], person_id=pid, line_item=f"ss_{kind}")
            cf.year_overrides = overrides
            if kind in ("own", "spousal"):
                cf.ss_info = {"kind": kind, "fra": bp.fra, "months_early": _months(start, bp.fra),
                              "to70": _months(bp.fra, (person.birth_year + 70, person.birth_month)),
                              "months_by_year": months_by_year}
            p.cashflows.append(cf)


def _recredit_ratio(info: dict, withheld_months: int) -> float:
    me = info["months_early"]
    if info["kind"] == "own":
        return own_factor(me - withheld_months, info["to70"]) / own_factor(me, info["to70"])
    return spousal_factor(me - withheld_months) / spousal_factor(me)


# =====================================================================================
# Claiming analysis
# =====================================================================================

def claiming_analysis(portfolio, scenario, ages=(62, 67, 70)) -> List[dict]:
    """Every combination of claiming ages (one per Social Security record), each run
    through the full plan. Returns one row per combination with lifetime benefits,
    taxes and net worth -- so the trade-off shows in the whole plan, not in isolation."""
    import copy
    import itertools
    import simulation
    recs = list(portfolio.social_security)
    if not recs:
        return []
    names = {x.person_id: x.name for x in portfolio.people}
    rows = []
    for combo in itertools.product(ages, repeat=len(recs)):
        sc = copy.deepcopy(scenario)
        sc.ss_overrides = {r.ss_id: {"claim_age_years": a, "claim_age_months": 0} for r, a in zip(recs, combo)}
        res = simulation.run_simulation(portfolio, sc)
        wp = res.working_portfolio
        ss = {y.year: sum(cf.annual_amount_in_year(y.year) for cf in wp.cashflows
                          if str(cf.flow_id).startswith("ss:") and simulation._flow_active_in_year(cf, y.year))
              for y in res.annual_results}
        withheld = sum(float(m.group(1).replace(",", "")) for y in res.annual_results for w in y.warnings
                       if (m := re.search(r"earnings test: \$([\d,]+)", w)))
        last = res.annual_results[-1]
        row = {f"{names.get(r.person_id, r.person_id)} claims at": a for r, a in zip(recs, combo)}
        row.update({"Lifetime Social Security": sum(ss.values()), "Withheld (earnings test)": withheld,
                    "Total tax": sum(y.total_estimated_tax for y in res.annual_results),
                    f"Net worth {last.year}": last.total_net_worth, "_by_year": ss})
        rows.append(row)
    return rows
