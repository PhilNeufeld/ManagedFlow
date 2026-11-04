# ManagedFlow roadmap

Agreed order of work. Each item ships with tests and, if the data model
changes, a schema migration so existing files keep loading.

## Done

| Version | Feature |
|---|---|
| 0.2 | One top-level cashflow list; schema migrations; test suite |
| 0.3 | Liabilities: `Loan` (amortizing), `CreditLine` (interest-only); net worth = assets - liabilities |
| 0.4 | Events with month-level dates; Monte Carlo; Claude Quick add; example household |
| 0.5 | `Person` class; age-based events; ages in results; survivor scenarios via events |
| 0.6 | `Membership` link class: people <-> entities many-to-many with roles |
| 0.7 | Ownership %: `Ownership` link class, share math with paths, your-share vs. 100% view |
| 0.8 | Actuals & forecasts: CSV import with matching, plan-vs-actual, trend breaks, used in simulations |
| 0.9 | Detailed tax engine (federal 2026 + CA 2025 tables) with depreciation, passive losses; taxes-by-year view |

## Next (in this order)

1. **How each account is taxed** -- taxable / tax-deferred / Roth on assets;
   withdrawal order by tax treatment; required minimum distributions.
2. **Social Security class** -- benefit at full retirement age + claiming age
   -> adjusted benefit; spousal and survivor rules.
3. **Spending policy** (lower priority) -- spending that adjusts to markets
   (guardrails), used by Monte Carlo.

## Open data tasks

- [ ] Confirm NFLP ownership: Philip's NFLP interest should be held by the
  Neufeld Family Trust of 2000 (TTEES Philip & Debbie Neufeld), so Debbie
  owns it jointly through the trust. ManagedFlow models it that way; confirm
  against the NFLP partnership records.

## Next after the portfolio is populated

- **Receivables** -- a note or fee you're OWED (the $50K LVM note; the LVM
  developer fee, $1,361,665 at 7.75% over 180 months): a balance paid down
  over time, like a loan in reverse, counted in net worth.
- **Allocated income vs. cash** -- LVM's tax distributions: taxable income
  allocated to you (K-1) can differ from the cash distributed.
- **Notes and tasks** -- dated notes and tasks (status, due date) attached to
  any record or to the portfolio.
- **Classes on actuals** (like QuickBooks classes) -- e.g. "Crop 2025" vs.
  "Crop 2026", since crop payments span calendar years; with notes per class.
- **Relative events** -- an event defined as an offset from another (e.g.
  each LVM distribution step relative to the start of distributions), so
  moving one event moves the whole schedule.

## Longer term

- **Social Security claiming analysis** -- compare claiming ages (62-70) for
  each spouse, including spousal/survivor effects, on lifetime income and net worth.
- **Tax impact analysis** -- started in v0.9 (Taxes by year table and chart).
  Still to do: per-property breakdown and "what if" comparisons (e.g. sell
  before vs. after depreciation runs out), depreciation recapture on sale.
- Tax engine extras: AMT, QBI deduction, IRMAA, CA exemption-credit phase-out,
  filing status change after a spouse's death.
- **Highest steady spending** -- solve for the largest inflation-adjusted
  spending level the plan can sustain for life.
- Monte Carlo upgrades: correlated asset swings; historical-sequence mode.
- Unitrust and pension valuation (present value of payments).
- Survivor rules built into pension and Social Security classes (benefit
  continues at a % with its accumulated COLA, instead of a separate cashflow).
- Actual account BALANCES (snapshots) vs. projected values -- today actuals
  cover cashflows only.
- In-app dictation (Win+H voice typing works today).
