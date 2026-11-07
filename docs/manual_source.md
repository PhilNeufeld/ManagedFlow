<!--
ManagedFlow user manual -- the ONE source for the Help tab and both Word editions.
Write generic text here. Lines like {{example:NAME}} are filled in from a portfolio by manual.py:
the generic edition uses the fictional example household; your private edition uses your file.
Formatting understood: # / ## / ### headings, paragraphs, "- " bullets, "1. " steps,
| tables |, ``` code blocks ```, "> " notes, **bold**.
Generated outputs: docs/USER_MANUAL.md and docs/ManagedFlow_Manual.docx (python docs/build_manual.py).
-->

# 1. What ManagedFlow does

ManagedFlow is a year-by-year plan for a household: what you own, what you owe, the money coming in and going out, and the events that change them (retiring, claiming Social Security, buying or selling a property, a loan closing). It projects every year to the end of the plan, estimates federal and California income tax, and shows the results as tables, charts and an Excel workbook.

Python does the calculations, your data is stored in a JSON file in the data folder, Streamlit is the screen you use, and Excel is the reporting layer.

{{example:household}}

## The tabs

| Tab | What it's for |
|---|---|
| Entities | Entities (you, trusts, LLCs, partnerships), Ownership %, People and who belongs to each entity |
| Bundles | Collections: optional groups of assets, cashflows and loans under one entity |
| Assets | What you own; the "Add a rental property" form |
| Liabilities | Loans (amortizing) and credit lines (interest-only, or converting to a loan after a draw period, like a HELOC) |
| Cashflows | Income and expenses, each optionally tied to an asset and a person |
| Actuals | Import what really happened (and near-term forecasts) from CSV; plan vs. actual; trend breaks |
| Events | Dated moments (retire, claim, buy, sell, close) that start and stop other records |
| Assumptions | Plan years, inflation, tax method, filing status, state, exchange rates |
| Scenarios | What-ifs: overrides to assumptions, assets, cashflows and event dates |
| Simulation | Run the plan. Sub-tabs: Results (table + chart, Years to view), Taxes by year, Rental pro forma, Spending (spending policy, highest steady spending), Social Security claiming, Monte Carlo, Warnings |
| Audit | The items behind every number in the results, for a year or a range, with checks that they add up |
| Explore | Drill down by entity > collection > asset > cashflow/loan, as a chart or a 1-5 year table |
| Help | This manual, on screen |
| Export | The full Excel workbook |

The "Quick add with Claude" box above the tabs turns a plain-English description into draft records.

# 2. The building blocks (classes)

Every record is one of these classes. The colors are the ones used on the Explore tab: entity blue, collection orange, asset aqua, liability yellow, cashflow pink, household green.

{{example:classes}}

# 3. Hierarchy: who owns what

Entities own other entities and assets through Ownership links. Your share of anything is the product of the percentages along each path, added up across paths. Amounts are shown in your share by default; a scenario can switch to the 100% view.

{{example:ownership_tree}}

{{example:share}}

> An entity you own through two paths appears twice on the Explore tab, once per path, each with that path's share. The paths add up to your total share, so nothing is counted twice.

## People and entities

People and entities are linked many-to-many by Membership, with a role (owner, trustee, manager, partner...). A personal entity has exactly one owner. Ownership % is separate: Membership says who is involved and how; Ownership says how much of an entity or asset another entity owns.

# 4. Assets and what belongs to them

An asset holds its own value and growth. Its revenue and expenses are cashflows linked to it, and its mortgage is a liability secured by it. No collection is needed: on Explore these appear indented under the asset, and when the asset is sold its loan is paid off with it.

| What | Where it lives | How it attaches |
|---|---|---|
| Value and growth | On the asset | - |
| Revenue (rent, crop sales) | Cashflow | Linked to the asset |
| Expenses (tax, insurance, repairs...) | Cashflow | Linked to the asset |
| Mortgage | Liability | Secured by the asset |

## Examples

{{example:assets}}

## A rental with a mortgage, step by step (illustrative figures)

```
4392 Sample St  [Asset]  value $400,000 · +3%/yr · depreciation $10,000/yr
├── Gross scheduled rent    [Cashflow]  $24,000
├── Vacancy & credit loss   [Cashflow]  ($1,200)
├── Property tax            [Cashflow]  ($4,000)
├── Insurance               [Cashflow]  ($1,200)
├── Repairs & maintenance   [Cashflow]  ($1,800)
├── Property management     [Cashflow]  ($1,920)
└── Sample St mortgage      [Liability] $251,057 · 6% · 240 payments left
```

Reading it: gross operating income (GOI) = $24,000 - $1,200 = $22,800. Net operating income (NOI) = $22,800 - $8,920 of expenses = $13,880. Debt service = $1,798.65 x 12 = $21,584. Cash flow before tax = $13,880 - $21,584 = ($7,704). Debt coverage = 13,880 / 21,584 = 0.64x: under 1.0x, the rent doesn't cover the mortgage.

# 5. Collections (bundles)

A collection belongs to one entity and groups several assets, plus any cashflows or loans that span them. It's a lens: it adds an orange section with a subtotal on the Explore tab but doesn't change any number in the plan.

## When to use one

- To subtotal several assets together (e.g. all your rentals).
- To group a cost or debt that spans assets and isn't secured by one (an umbrella insurance policy, an equipment line of credit).
- Not needed for a single asset: its cashflows and loan already belong to it.

## Examples

{{example:collections}}

> Put each asset in at most one collection, so subtotals don't count it twice.

# 6. The Explore tab

## Chart view

- Click any box to drill in; the path buttons across the top go back up. "Or go to" does the same from the keyboard.
- The current level is on the left; three levels of what's inside it run to the right.
- Box size: number of items, or dollars (value + balance + cash in and out) for a chosen year.
- Below the chart: what's inside (this year's assets, liabilities, cash in, cash out), value over time, net cash flow by year, and the full year-by-year table.

## Table view (period view)

- One row per asset, liability and cashflow under the current level, for 1 to 5 years.
- Grouped by collection, else by entity; household items by person ("Household: <name>", "Shared").
- An Events row across the top, one cell per year.
- Assets show year-end value; liabilities show balance; cashflows show the year's amount (outflows in parentheses).
- ▲/▼ marks a year that changed from the year before by at least 10% and $5,000 (both editable). A checkbox shows only rows with a substantial change or an event.
- Notes say what drove a change: starts, ends, one-time, sold/repaid, paid off, earnings-test withholding.
- Group subtotals (net worth, net cash flow after debt service) and totals that match the Simulation tab. The Excel export has the same table as a "Period View" sheet.

{{example:events}}

# 7. Taxes and Social Security

The detailed tax method estimates federal (2026 tables) and California (2025 tables) income tax each year, indexed by your inflation assumption after the table year.

- Wages, pensions, interest, unitrusts and other ordinary income; capital gains and qualified dividends at their rates.
- Rentals and businesses are taxed on NET income: receipts - deductible expenses - loan interest - depreciation. Rental losses follow the passive-loss rules and carry forward.
- Payroll and self-employment tax are worked out PER PERSON: each spouse has their own Social Security wage base ($184,500 in 2026, $192,300 in 2027). Each wage, fee or business is assigned to a person (default: the first person).
- Standard vs. itemized deduction with the SALT cap; 65+ additions; the 2025-2028 senior deduction; taxable Social Security; NIIT; additional Medicare.

## Social Security earnings test

If someone claims before full retirement age (FRA: 67 for anyone born 1960 or later) and keeps working, SSA withholds benefits: $1 for every $2 of earnings over $25,560 (2027); in the year FRA is reached, $1 for every $3 over $67,920, counting only months before FRA. Earnings = wages + net self-employment earnings. In the first year of benefits, a month with low wages and no substantial self-employment work is paid in full. Withheld months are credited back at FRA: for benefits set up under Social Security (Entities > People), the benefit is recomputed as if claimed that many months later.

{{example:earnings_test}}

## Not modeled yet

- AMT, QBI deduction, IRMAA, most credits, depreciation recapture on sale; capital gains on taxable-account withdrawals.
- Allocated partnership income (K-1) that differs from the cash distributed.
- For Social Security entered as a plain cashflow (instead of under People): the credit-back at FRA, spousal and survivor rules.

# 8. Saving, backups and archiving a data set

## Everyday saving

- **Save portfolio** (sidebar) writes your file, e.g. data\MyPlan.json. Before overwriting, it copies the old version to a timestamped backup in the same folder: MyPlan.backup_20261104_153012.json.
- **Save backup copy now** (sidebar) makes a timestamped backup on demand, without saving changes.
- **If the file changed on disk** since you loaded it (a script or another window updated it), the sidebar says so and offers **Reload from disk**. **Save portfolio** then stops and asks: **Reload from disk** (drops your unsaved changes), **Overwrite anyway**, or **Cancel** -- so a save never silently undoes someone else's changes.
- Household files are kept off GitHub: .gitignore covers your portfolio, data\*.csv, backups, data\*_tasks.md, data\build_*.py and outputs\. Back them up off this computer (next section).
- **Move save copies to data\Archive** (sidebar, under Backup) clears the timestamped copies out of the data folder; they stay in data\Archive.

## Backing up off this computer

The timestamped copies sit on the same disk as your file, so a failed drive takes them too. The sidebar's **Backup** section zips every private file -- portfolios, actuals CSVs, builder scripts, task lists and the outputs folder -- into one dated file, e.g. ManagedFlow_backup_2026-11-06_083000.zip, in a folder you choose. The code itself is on GitHub.

1. Pick a folder that Google Drive, OneDrive or Dropbox syncs, e.g. G:\My Drive\Backups\ManagedFlow, type it under **Backup folder**, and click **Save settings**. The setting is stored in .managedflow\settings.json on this computer only.
2. Click **Back up now** to make the first backup.
3. With **Back up automatically on the first save each day** on, the first **Save portfolio** of each day also makes a backup. Only the newest backups are kept (30 by default); the sync service keeps older versions too.
4. If the drive isn't there (for example Google Drive isn't running), you get a message instead of a backup -- start the sync app and click **Back up now**.

To restore: under **Restore**, pick a backup, look at **What's inside**, tick the confirmation and click **Restore**. Your current files are copied to data\Archive\pre_restore_<time> first, so a restore can be undone. Then load the portfolio file again.

> Backups hold your full financial picture. Keep the sync account protected with two-step verification.

## Archiving a data set (e.g. a year-end snapshot)

1. Click **Save portfolio** so the file on disk is current.
2. In File Explorer, create a folder data\Archive inside the ManagedFlow folder (once).
3. Copy your portfolio file into it and rename the copy with its date and purpose, e.g. MyPlan_2026-12-31_year-end.json.
4. Optionally export to Excel (Export tab) and save the workbook next to it as a read-only record of the results.
5. The next backup (sidebar, Backup) includes it: snapshots in data\Archive are zipped along with everything else (the automatic save copies are not).

> The sidebar lists only files directly in the data folder, so archived files stay out of the list. Older files load fine: ManagedFlow upgrades the schema as it opens them.

## Restoring an archived or backup version

1. Copy the archived or backup file into the data folder (for an archive, give it a new name such as MyPlan_restored.json).
2. In the sidebar, choose it under "Open an existing portfolio file" and click **Load selected file**.
3. Check it, then **Save portfolio**. To make it your main file again, rename it to your usual file name.

# 9. How do I... (FAQ)

## Add a rental property with a mortgage?

1. Assets tab > "Add a rental property (rent, expenses and mortgage)".
2. Enter the name, entity, market value, gross scheduled rent per year (fully leased), vacancy %, and rent and expense growth.
3. Enter each operating expense per year: property tax, insurance, repairs, management, utilities, HOA, other.
4. Enter the depreciable basis (building and improvements, not land) and the year placed in service.
5. Mortgage: either A, the original terms (amount, years, first payment month and year), or B, the current balance and years left. Enter the principal-and-interest payment from your statement if it differs. Leave both blank for no mortgage.
6. Click **Add rental**, then **Save portfolio**. Simulation tab > Rental pro forma sub-tab shows GOI, NOI, debt service and cash flow by year.

> Put escrowed property tax and insurance in the expense lines, not in the mortgage payment, or they'll be counted twice.

## Enter a HELOC that becomes a loan after its draw period?

1. Liabilities tab > add a **credit line** with today's balance and rate (and the credit limit, if you like).
2. Under **Draw period end**, set **Becomes a loan in** (year and month of the first amortizing payment) and **Repaid over** (years), e.g. 2035, Apr, 15 for a 10-year draw period followed by a 15-year repayment.
3. Until then it's interest-only; from that month the same balance is repaid in equal monthly payments, so it reaches $0 on schedule with no lump sum. The Liabilities table shows both payments and the last payment date.

## Track an account in another currency (e.g. a Canadian bank account)?

1. Assumptions tab > **Exchange rates**: add the currency and its rate in US dollars per 1 unit (e.g. CAD 0.72), then **Save exchange rates**.
2. Assets tab: add the account with **Currency** = CAD and its balance in CAD. Cashflows linked to it are also entered in CAD.
3. For a bank account, CD or money market, tick **Growth is interest, taxed every year**: the balance compounds, and each year's interest is taxed (ordinary income and NIIT) without counting as spendable cash.
4. Every run converts it to US dollars at the rate on the Assumptions tab; all totals, taxes and net worth are in USD. The Assets table shows the balance in CAD with its USD equivalent. Change the rate (or try another in a scenario) to see the effect of the exchange rate.

## Enter a loan from the original note?

1. Liabilities tab > Add new liability > Loan.
2. Enter the name, type, lender and interest rate; set "Balance as of (year)" to the plan's first year.
3. Under Original terms enter the original amount, term in years and first payment month and year, and tick "Work out balance and months left from the original terms".
4. Pick the asset that secures it and Save. The table shows payments left and the last payment date.

> Example: $300,000 at 6% for 30 years from Jan 2016 gives a Jan 2026 balance of $251,057 with 240 payments left (last Dec 2045).

## Model an annuity?

1. Assets tab > Add new asset: type annuity, its current value, liquidity "income only", tax category annuity income.
2. Cashflows tab > Add new cashflow: type annuity income, linked to the annuity, monthly amount, a start event (e.g. "Annuity payments start"), growth if it has a COLA, and the person it belongs to.
3. Taxes: for a non-qualified annuity only part of each payment is taxable (the exclusion ratio). Enter two cashflows: the taxable part (taxable on) and the return of principal (taxable off).
4. If payments stop at death or after a term, give them an end event or end year.

## Model a pension with a COLA and a survivor benefit?

1. Cashflows: type pension income, monthly amount, the person, growth = the COLA (e.g. 2%), start event = retirement.
2. Create an event for the pensioner's death at an age, and make it the pension's end event.
3. Add a second cashflow for the survivor benefit (e.g. 50%) starting at that event, for the spouse.
4. A scenario can move the death event to test early or late survivorship.

## Add Social Security?

1. Entities tab > People > "Social Security (from your SSA statement)". For each person enter the PIA -- the monthly benefit at full retirement age on the SSA statement, in the plan's first-year dollars -- the claiming age (62-70, plus months) and the COLA.
2. ManagedFlow applies SSA's rules: 5/9 of 1% less per month claimed early (5/12 of 1% beyond 36 months), 2/3 of 1% more per month after full retirement age up to 70; a spousal top-up to half the spouse's PIA (reduced if claimed early; it starts once both have filed); and at a death (plan-to age) the survivor keeps the larger benefit.
3. The earnings test applies automatically to anyone claiming before FRA while working, and at FRA the benefit is recomputed to credit back the withheld months. In the person's editor, untick "Works substantially in their own business" if they don't (generally over 45 hours a month).
4. Don't also enter Social Security as a cashflow -- the screen warns if both exist.

## Decide when to claim Social Security?

1. Simulation tab > Social Security claiming sub-tab: pick the ages to compare (e.g. 62, 67, 70) and click **Compare**.
2. The whole plan runs once for every combination of both spouses' ages. Each row shows lifetime benefits, how much the earnings test withheld, total tax and net worth at the end of the plan; ◀ marks your current choice.
3. The chart shows cumulative benefits by year for the options you pick: where the lines cross is the break-even age.
4. To keep a choice, change the claiming age in the person's Social Security settings -- or make a scenario for it.

## Set up IRAs, Roth and brokerage accounts?

1. Assets tab > the account's editor: set **Account type** -- Taxable (brokerage, savings), Tax-deferred (traditional IRA, 401k) or Roth -- and the **Account owner**.
2. Liquidity "liquid" or "semi-liquid" lets the plan draw on it to cover a shortfall. Within a liquidity group it draws taxable accounts first, then tax-deferred, then Roth last.
3. A withdrawal from a tax-deferred account is ordinary income: ManagedFlow re-estimates the tax and withdraws a little more to pay it (grossing up). Roth and taxable-account withdrawals aren't income (capital gains in a taxable account aren't modeled yet).
4. Required minimum distributions start automatically at the owner's RMD age -- 75 if born 1960 or later, 73 if born 1951-59, 72 if earlier: each year the prior year-end balance / the IRS Uniform Lifetime Table factor (24.6 at 75, 12.2 at 90...) leaves the account as taxable income. Roth accounts have none.

## Model a note or fee someone owes me (a receivable)?

1. Assets tab > Add new asset, type **receivable**. Value = the balance owed when payments start.
2. Under Receivable terms: interest rate, number of monthly payments (or the payment), and the first payment month and year -- or an event (e.g. "Loan closes").
3. Tick "Principal is taxable income" for a fee earned but paid later (choose how it's taxed, e.g. self-employment income) -- leave it unticked for a loan you made, whose principal is a tax-free return of your money.
4. Each year ManagedFlow splits the payments into interest (taxed as interest) and principal, and the asset's value is the balance still owed, counted in net worth. It's never drawn on to cover a shortfall.

## Model a salary that ends at retirement?

1. Events: create "Retires" with the month (or an age).
2. Cashflows: type salary, monthly or annual amount, the person, end event = Retires (it stops the month before).

## Model a farm or business?

1. Asset: type business, tax category business income, tick "Self-employment tax on its net income", and choose whose self-employment income it is.
2. Revenue cashflows linked to the asset (e.g. crop sales) and expense cashflows of type operating expense, so its net is what's taxed.
3. Use Actuals with a class (e.g. "Crop 2025", "Crop 2026") for payments that span calendar years.

## Show something owned through an entity?

1. Entities tab: create the entity (e.g. an LLC or partnership).
2. Ownership %: add who owns it and what percent.
3. Put the asset under that entity. "Explain a share" shows every path and your total share.

## Sell a property?

1. Events: create "Sell <property>" with the month.
2. Asset editor: set "Sold at event" to it. Its loans are paid off that month and its value goes to zero.
3. Add a one-time sale proceeds cashflow for the net cash, and a capital-gains cashflow for the taxable gain (recapture isn't modeled yet).

## Record what actually happened (actuals)?

1. Actuals tab: pick the year and kind, and download "Template for this portfolio (with keys)". It lists every planned cashflow with its **cashflow_id** -- the cashflow's key.
2. Fill in the amounts (rows left blank are skipped). kind = actual (what happened) or forecast (a near-term estimate); status = confirmed, provisional or estimated; class groups items such as "Crop 2026".
3. For anything not in the plan, add a row with no cashflow_id: give its name, category, direction and amount, and asset_id if it belongs to an asset.
4. Upload it. Each row is linked by its cashflow_id; check the review grid (you can pick a cashflow, by key, for a row that has none), then Import.

> Rows are linked by key, never by name: a row named exactly like a planned cashflow but without its cashflow_id stays "not in the plan".

## Do actuals replace forecasts and the plan?

Yes, item by item and year by year: **actual beats forecast, and forecast beats plan**.

- For each planned cashflow and year, a full-year **actual** replaces the plan. With no actual, a full-year **forecast** replaces it. With neither, the plan is used.
- Monthly records work month by month: an actual month beats a forecast month, and months with no record are filled from the plan (shown as "actual + plan").
- Several records of the same kind for one cashflow and year add up (e.g. crop sales + crop insurance).
- Only years with a record change. A 2027 forecast changes 2027; 2028 onward follows the plan.
- This applies everywhere results appear: Simulation, Explore (chart and table), the rental pro forma and the Excel export, in any scenario with **Use actuals** ticked. Untick it (e.g. a "Plan only" scenario) to see the plan alone.
- Records not linked to a planned cashflow ("not in the plan") count **once, in their own year**, on their asset (or the household): a surprise $4,200 repair lowers that year's cash flow, and is deducted if the asset is a rental or business; it never repeats. A scenario setting, "Count unplanned actuals", turns this off. They're also flagged ⚠ in the plan-vs-actual report.
- Social Security: the earnings test is applied to plan and forecast amounts, but not to a recorded actual (an actual is what was really paid).

## Link a record I already imported to the plan?

1. Actuals tab: a warning lists any records not linked to a planned cashflow.
2. Open "Link a record to a planned cashflow", pick the record and its planned cashflow (by key), and click **Save link**. Choose "(not in the plan)" to unlink.
3. Click **Save portfolio**. The record now replaces the plan for its cashflow and year instead of counting as a one-off.

## What are the keys (IDs)?

Every table -- entities, people, events, assets, liabilities, cashflows, actuals, collections, scenarios -- has an integer primary key (1, 2, 3... per table). Links between records always use these keys (a cashflow's asset_id is the asset's key), never names, so renaming a record never breaks a link. Keys are assigned automatically and never change. The link tables have their own integer keys too (membership_id, ownership_id), and the pair of records each one joins must be unique: one ownership link per owner and owned item; one membership per entity, person and role.

## Read the Simulation results?

- **Expenses** include **Debt Service** (the Debt Service column is shown for information; it isn't subtracted twice).
- **Surplus/(Deficit)** = Gross Income - Expenses - Est. Tax. A surplus is reinvested in the first liquid asset (e.g. your brokerage account) and grows at that asset's rate; a deficit is withdrawn from liquid, then semi-liquid assets (taxable accounts first, Roth last).
- **Liquid Assets** = liquid + semi-liquid assets (brokerage, IRAs); **Illiquid Assets** = everything else (real estate, businesses, trusts, receivables). Neither subtracts debt; Total Net Worth does.
- **Years to view** (under the table) limits the chart to the first N years (10 by default), so early changes aren't flattened by the scale of later decades.

## Audit a number (where does a total come from)?

1. Simulation tab > **Run simulation for all scenarios**.
2. Audit tab: pick the scenario, **From year** and **To year** (one year or a range), and optionally an **Entity**.
3. The banner says whether every check adds up. **Summary & checks** lists each total from the results next to the sum of its items.
4. **By asset** puts each asset (or bundle, or entity -- **Group by**) side by side: gross income, expenses, net operating income, interest, principal, cash flow before tax, depreciation, taxable net, est. tax and net after tax, summed over the years chosen. Tax is figured for the whole household, so each asset's share is **allocated**: its taxable net (rentals and businesses: income - expenses - interest - depreciation) x the household's average rate that year; a tax loss shows as a saving. A Household row carries salaries, Social Security, household spending and the rest of the tax, so the Total matches the results. Pick one under **Look at one ... year by year** to see its statement by year and every item behind it -- e.g. a rental, or the whole pistachio crop.
5. The section tabs show the items, one row each with its **Source ID** (cashflow key, loan:<key> or asset:<key>) and a column per year:
   - **Gross income** and **Expenses** -- every inflow and outflow; Expenses also lists each loan's payments.
   - **Debt service** -- each loan's beginning balance, interest, principal and ending balance.
   - **Est. tax** -- the calculation step by step: income by category, AGI, deductions, federal, payroll per person, California, total.
   - **Cash** -- surplus/(deficit) and where it went (reinvested or withdrawn, by asset).
   - **Assets** -- each asset's roll-forward: beginning value + growth + value-changing cashflows + other changes (RMD, receivable paydown, sale) - withdrawn + reinvested = ending value.
6. **Download to Excel** saves what you're looking at as a workbook: Summary & Checks, a sheet per section (with Total rows as SUM formulas), By Asset (the side-by-side table, then each asset year by year) and All Items (one row per item, for filters and pivots). The Excel export has the same items for every year and scenario on the **Audit** sheet, and the checks on **Audit Checks**.

## Let spending adjust to markets (guardrails)?

1. Scenarios tab: edit (or add) a scenario and set **Spending policy** to **Guardrails**.
2. Set the guardrails (defaults: cut 10% when the draw rate is above 6%; restore when it is below 3%; floor 80% of the plan; ceiling 100%) and whether to **skip the inflation raise after a losing year**.
3. The **draw rate** is last year's withdrawals from liquid savings (less any surplus reinvested) divided by liquid assets. It only applies to living expenses (cashflows of type living_expense); years with recorded actuals are never changed.
4. Simulation > **Spending** shows living expenses each year, the multiplier on the plan and what changed. Monte Carlo then shows the spread of spending and the share of runs that cut at least once.

If your income covers your spending (the draw rate is zero or negative), guardrails never cut. Set the ceiling above 100% to let spending rise in good years.

## Find the most I can spend (highest steady spending)?

1. Simulation > **Spending** > **Highest steady spending**: pick the scenario and, optionally, a cash reserve to keep (today's dollars).
2. **Find the highest steady spending** solves for the largest first-year living-expense level, growing as your living expenses do, that the plan sustains through its last year: no year with a shortfall the liquid assets can't cover, and liquid assets never below the reserve. It shows the headroom over your plan and the year that would fail first just above it.
3. For a market-tested answer, set **Monte Carlo runs** (e.g. 100-200) and a **success target** (e.g. 90%): it finds the level that succeeds in that share of random-market runs. Expect a lower number, and about 30 seconds per 100 runs.

## Read the rental pro forma's bottom lines?

- **NOI** is the property's EBITDA: rent less operating expenses, before interest, taxes and depreciation.
- **Cash flow before tax** = NOI - interest - principal.
- **Taxable net** = NOI - interest - depreciation: what the IRS taxes (depreciation isn't cash).
- **Est. income tax** = taxable net x your household's average tax rate that year (a loss shows as a saving), so **Cash flow after tax** = cash flow before tax - that tax.

## Run a what-if?

1. Scenarios tab > add a scenario. Override inflation, an asset's growth or sale, a cashflow's amount, or an event's date (e.g. claim Social Security at 67).
2. Untick "Use actuals" to see the plan alone; tick the 100% view to see whole properties instead of your share.
3. Simulation tab > run all scenarios and compare.

## Test the plan against market swings (Monte Carlo)?

1. Simulation tab > Monte Carlo sub-tab: choose the number of runs and press Run.
2. Read the success rate (runs that never run short) and the percentile bands of net worth.

## Use Quick add?

1. Open "Quick add with Claude" above the tabs and describe it, e.g. "Sam gets a pension of $20,000 a year from July 2030, with a 2% COLA".
2. Review the drafted records and add them. Needs the ANTHROPIC_API_KEY setting.

## Export to Excel?

1. Export tab > Export. The workbook includes inputs, annual results, tax detail, actuals, the rental pro forma and the period view.

## Rebuild or print this manual?

1. Help tab > build and download the Word manual: generic, or with your data. Or run: python docs/build_manual.py (generic) or python docs/build_manual.py data/MyPlan.json (yours, written to outputs\).
2. In Word, right-click the contents list and choose Update Field; File > Save As > PDF for a PDF.
3. To change the manual, edit docs/manual_source.md; both editions and the Help tab follow.

## Run the tests?

1. In PowerShell in the ManagedFlow folder with the venv active: python -m pytest (all) or python -m pytest -m "not ui" (skip the slower screen tests).

# 10. Roadmap

{{example:roadmap}}

{{example:tasks}}

# Appendix: Glossary

| Term | Meaning |
|---|---|
| GOI | Gross operating income: gross scheduled rent less vacancy and credit loss |
| NOI | Net operating income: GOI less operating expenses (before debt service and tax) |
| Debt service | Loan payments: interest + principal |
| Debt coverage (DSCR) | NOI / debt service; under 1.0x the property doesn't cover its loan |
| FRA | Full retirement age for Social Security (67 if born 1960 or later) |
| Earnings test | Withholding of Social Security benefits when earning above a limit before FRA |
| Wage base | Earnings subject to the 6.2% / 12.4% Social Security tax, per person per year |
| NESE | Net earnings from self-employment: 92.35% of net self-employment profit |
| Your share | Amounts multiplied by your ownership along every path to the item |
| Collection | A bundle: an optional group of records under one entity |
| Actual / forecast | A recorded amount for a past year / a near-term estimate; both replace the plan for their year |
| Status | How sure a recorded amount is: confirmed, provisional or estimated |
| Highest steady spending | The largest inflation-adjusted yearly spending the plan can sustain for life (roadmap) |
