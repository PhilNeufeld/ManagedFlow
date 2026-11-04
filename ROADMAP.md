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

## Next (in this order)

1. **Tax engine with depreciation** -- federal + California brackets, standard
   deduction, capital gains rates, Social Security taxation, NIIT; depreciation
   and mortgage interest offsetting rental income.
2. **How each account is taxed** -- taxable / tax-deferred / Roth on assets;
   withdrawal order by tax treatment; required minimum distributions.
3. **Social Security class** -- benefit at full retirement age + claiming age
   -> adjusted benefit; spousal and survivor rules.
4. **Actual vs. planned** -- record actual balances and amounts (`Snapshot`),
   CSV import; flag where actuals differ from plan (e.g. crop payments), and
   where next year's plan breaks from the long-run trend of a curve.
5. **Spending policy** (lower priority) -- spending that adjusts to markets
   (guardrails), used by Monte Carlo.

## Longer term

- **Social Security claiming analysis** -- compare claiming ages (62-70) for
  each spouse, including spousal/survivor effects, on lifetime income and net worth.
- **Tax impact analysis** -- show year by year how deductions change taxable
  income, e.g. when a rental's depreciation runs out (27.5 years residential,
  39 commercial) or a mortgage's interest shrinks.
- **Highest steady spending** -- solve for the largest inflation-adjusted
  spending level the plan can sustain for life.
- Monte Carlo upgrades: correlated asset swings; historical-sequence mode.
- Unitrust and pension valuation (present value of payments).
- Survivor rules built into pension and Social Security classes (benefit
  continues at a % with its accumulated COLA, instead of a separate cashflow).
- In-app dictation (Win+H voice typing works today).
