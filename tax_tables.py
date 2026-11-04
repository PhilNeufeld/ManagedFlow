"""
tax_tables.py

Every tax number the detailed engine uses, in one place, with its source.
UPDATE THIS FILE EACH YEAR when the IRS (October) and California FTB
(late in the year) publish new figures; nothing else should need to change.

Years after a table's year are indexed by the plan's inflation assumption
(index_amount). That's an approximation: real brackets are indexed by
chained CPI and rounded.

Sources (checked November 2026):
- Federal 2026 brackets and standard deduction: IRS inflation adjustments
  for tax year 2026 (Rev. Proc. 2025-32), as reported by the Journal of
  Accountancy, Oct 2025.
- 2026 long-term capital gains thresholds: Kiplinger, "IRS Updates Capital
  Gains Tax Thresholds for 2026".
- 2026 additional standard deduction (65+), senior deduction and phase-out,
  SALT cap and phase-down, NIIT thresholds, Social Security taxability
  thresholds: Baird "2026 Tax Facts".
- 2026 Social Security wage base $184,500: SSA, via Journal of Accountancy.
- California 2025 Tax Rate Schedules X and Y: FTB form 2025-540 tax rate
  schedules (ftb.ca.gov).
- California 2025 standard deduction and $153 personal exemption credit:
  FTB Form 540 instructions (as summarized by reedcorp.tax).
"""

from __future__ import annotations

FEDERAL_YEAR = 2026
CALIFORNIA_YEAR = 2025

# (upper bound of bracket, rate); the last bound is None = no limit
FEDERAL_BRACKETS = {
    "married_joint": [(24_800, 0.10), (100_800, 0.12), (211_400, 0.22), (403_550, 0.24),
                      (512_450, 0.32), (768_700, 0.35), (None, 0.37)],
    "single": [(12_400, 0.10), (50_400, 0.12), (105_700, 0.22), (201_775, 0.24),
               (256_225, 0.32), (640_600, 0.35), (None, 0.37)],
}
STANDARD_DEDUCTION = {"married_joint": 32_200, "single": 16_100}
ADDITIONAL_65_PER_PERSON = {"married_joint": 1_650, "single": 2_050}

# One Big Beautiful Bill Act senior deduction: $6,000 per person 65+, tax
# years 2025-2028, reduced by 6% of MAGI over the threshold.
SENIOR_DEDUCTION = 6_000
SENIOR_DEDUCTION_YEARS = range(2025, 2029)
SENIOR_PHASEOUT_START = {"married_joint": 150_000, "single": 75_000}
SENIOR_PHASEOUT_RATE = 0.06

# Long-term capital gains / qualified dividends: top of the 0% and 15% bands
CAPITAL_GAINS_BANDS = {
    "married_joint": [(98_900, 0.0), (613_700, 0.15), (None, 0.20)],
    "single": [(49_450, 0.0), (545_500, 0.15), (None, 0.20)],
}

# SALT cap: $40,400 in 2026, +1%/yr through 2029, then back to $10,000 (2030+).
# Reduced by 30% of MAGI over the threshold, but not below $10,000.
SALT_CAP_2026 = 40_400
SALT_PHASEDOWN_START_2026 = 505_000
SALT_PHASEDOWN_RATE = 0.30
SALT_FLOOR = 10_000

NIIT_RATE = 0.038
NIIT_THRESHOLD = {"married_joint": 250_000, "single": 200_000}           # not indexed
ADDITIONAL_MEDICARE_RATE = 0.009
ADDITIONAL_MEDICARE_THRESHOLD = {"married_joint": 250_000, "single": 200_000}  # not indexed

# Social Security benefit taxation (provisional income), not indexed
SS_BASE = {"married_joint": (32_000, 44_000, 6_000), "single": (25_000, 34_000, 4_500)}

SOCIAL_SECURITY_WAGE_BASE = 184_500
SOCIAL_SECURITY_RATE = 0.062     # employee share; self-employed pay 2x
MEDICARE_RATE = 0.0145
SE_EARNINGS_FACTOR = 0.9235

# Rental passive-loss special allowance (active participation), not indexed
PASSIVE_ALLOWANCE = 25_000
PASSIVE_PHASEOUT_START = 100_000
PASSIVE_PHASEOUT_RATE = 0.50

# --- California (2025 schedules) -----------------------------------------------
CA_BRACKETS = {
    "married_joint": [(22_158, 0.01), (52_528, 0.02), (82_904, 0.04), (115_084, 0.06),
                      (145_448, 0.08), (742_958, 0.093), (891_542, 0.103), (1_485_906, 0.113), (None, 0.123)],
    "single": [(11_079, 0.01), (26_264, 0.02), (41_452, 0.04), (57_542, 0.06),
               (72_724, 0.08), (371_479, 0.093), (445_771, 0.103), (742_953, 0.113), (None, 0.123)],
}
CA_STANDARD_DEDUCTION = {"married_joint": 11_412, "single": 5_706}
CA_PERSONAL_CREDIT = 153          # per filer
CA_SENIOR_CREDIT = 153            # per filer 65+ (same amount as the personal credit)
CA_MENTAL_HEALTH_RATE = 0.01      # extra 1% on taxable income over $1M, not indexed
CA_MENTAL_HEALTH_THRESHOLD = 1_000_000


def index_amount(amount: float, table_year: int, year: int, inflation: float) -> float:
    """Grow a table amount from its year to `year` by the inflation assumption."""
    if year <= table_year:
        return amount
    return amount * (1 + inflation) ** (year - table_year)


def indexed_brackets(brackets, table_year: int, year: int, inflation: float):
    return [(None if top is None else index_amount(top, table_year, year, inflation), rate) for top, rate in brackets]


def tax_from_brackets(income: float, brackets) -> float:
    """Progressive tax: each slice of income taxed at its bracket's rate."""
    tax, lower = 0.0, 0.0
    for top, rate in brackets:
        if income <= lower:
            break
        upper = income if top is None else min(income, top)
        tax += (upper - lower) * rate
        if top is None or income <= top:
            break
        lower = top
    return tax


def salt_cap(year: int, magi: float, inflation: float) -> float:
    if year >= 2030:
        return SALT_FLOOR
    cap = SALT_CAP_2026 * 1.01 ** max(0, year - 2026)
    start = SALT_PHASEDOWN_START_2026 * 1.01 ** max(0, year - 2026)
    return max(SALT_FLOOR, cap - SALT_PHASEDOWN_RATE * max(0.0, magi - start))
