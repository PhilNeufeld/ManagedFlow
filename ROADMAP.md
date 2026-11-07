# ManagedFlow roadmap

Agreed order of work. Each item ships with tests and, if the data model
changes, a schema migration so existing files keep loading.

Household-specific to-dos live next to each portfolio in `data/<file>_tasks.md`
(kept off GitHub), not here.

## Done

| Version | Feature |
|---|---|
| 0.2 | One top-level cashflow list; schema migrations; test suite |
| 0.3 | Liabilities: `Loan` (amortizing), `CreditLine` (interest-only); net worth = assets - liabilities |
| 0.4 | Events with month-level dates; Monte Carlo; Claude Quick add; example household |
| 0.5 | `Person` class; age-based events; ages in results; survivor scenarios via events |
| 0.6 | `Membership` link class: people <-> entities many-to-many with roles |
| 0.7 | Ownership %: `Ownership` link class, share math with paths, your-share vs. 100% view |
| 0.8 | Actuals & forecasts: CSV import with matching, plan-vs-actual, trend breaks, used in simulations; status and class on each record |
| 0.9 | Detailed tax engine (federal 2026 + CA 2025 tables) with depreciation, passive losses; taxes-by-year view |
| 0.9+ | Rentals from gross operating income with mortgages ("Add a rental property"); loans from their original terms; rental pro forma |
| 0.9+ | Per-person payroll and self-employment tax (each spouse's own wage base); Social Security earnings test |
| 0.9+ | Explore tab: drill-down chart (entity > collection > asset > cashflow/loan) and 1-5 year period table with events; Period View export |
| 0.9+ | Help tab and the user manual, built from one source: a generic edition (example household) and a private edition (your portfolio) |
| 1.2 | Accounts: taxable / tax-deferred / Roth with an owner; required minimum distributions (SECURE 2.0 ages, Uniform Lifetime Table); shortfalls drawn taxable -> tax-deferred -> Roth; tax-deferred withdrawals taxed and grossed up |
| 1.2 | Social Security class: PIA + claiming age by SSA's rules; spousal and survivor benefits; earnings-test months credited back at FRA; claiming-age comparison through the whole plan. Receivables: notes and fees owed to you, paid down monthly, interest and principal taxed separately |
| 1.1 | Link tables (memberships, ownerships) get integer primary keys; the joined pair stays a unique constraint; edit in place |
| 1.2 | Assets in another currency (value and cashflows converted to USD at Assumptions exchange rates); interest taxed yearly on bank/CD accounts |
| 1.2 | Credit lines that convert to an amortizing loan after a draw period (HELOC: interest-only, then repaid over N years) |
| 1.2 | Audit: the simulation records every item behind each annual total; Audit tab (year or range, by entity, with checks that items add back to the totals) and Audit / Audit Checks sheets in the Excel export. Simulation tab split into sub-tabs; chart Years to view; Liquid/Illiquid Assets labels |
| 1.2 | Off-machine backups: one dated zip of all private files (portfolios, actuals, builder scripts, outputs) to a synced folder; daily automatic backup on save; keep the newest N; restore with the current files set aside; tidy save copies into data/Archive |
| 1.0 | Integer primary keys in every table (migration renumbers older files); actuals import links by key (cashflow_id), never by name, with a template listing each planned cashflow's key; unplanned actuals count once in their own year; re-link stored records |

## Next (in this order)

1. **Spending policy** (lower priority) -- spending that adjusts to markets
   (guardrails), used by Monte Carlo.

## Next after a portfolio is populated

- **Allocated income vs. cash** -- partnership income allocated to you (K-1)
  can differ from the cash distributed; tax the allocation, count the cash.
- **Notes and tasks** -- dated notes and tasks (status, due date) attached to
  any record or to the portfolio.
- **Notes on classes** -- a note per actuals class (e.g. "Crop 2026: crop failure").
- **Relative events** -- an event defined as an offset from another (e.g.
  each step of a distribution schedule relative to its start), so moving one
  event moves the whole schedule.

## Longer term

- Accounts: cost basis and capital gains on taxable-account withdrawals; Roth conversions
  (how much to convert each year to fill a tax bracket); inherited IRAs (10-year rule).

- Social Security extras: benefits for a divorced spouse; a survivor claiming before
  their own full retirement age; family maximum.
- **Tax impact analysis** -- per-property breakdown and "what if" comparisons
  (e.g. sell before vs. after depreciation runs out), depreciation recapture on sale.
- Tax engine extras: AMT, QBI deduction, IRMAA, CA exemption-credit phase-out,
  filing status change after a spouse's death.
- **Highest steady spending** -- solve for the largest inflation-adjusted
  spending level the plan can sustain for life.
- Monte Carlo upgrades: correlated asset swings; historical-sequence mode.
- Unitrust and pension valuation (present value of payments).
- Survivor rules built into pension and Social Security classes.
- **Actual account balances** (snapshots) vs. projected values -- today
  actuals cover cashflows only.
- In-app dictation (Win+H voice typing works today).
