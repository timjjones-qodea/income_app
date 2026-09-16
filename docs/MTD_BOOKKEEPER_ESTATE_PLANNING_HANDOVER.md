# MTD Bookkeeper handover: household income, expenditure and gifts

**Prepared:** 15 September 2026  
**Source system:** INCOME app retirement and estate-planning work  
**Target system:** MTD Bookkeeper  
**Baseline tax year:** 2026/27 (6 April 2026 to 5 April 2027)  
**Status:** implementation handover; tax/legal classifications remain subject to professional review

## 1. Purpose

Extend MTD Bookkeeper so Tim and Wendy can build and maintain an auditable record of:

- household income received;
- normal household expenditure;
- capital movements;
- gifts made by each donor;
- the annual surplus of income over normal expenditure;
- the evidence needed to support a future claim for the Inheritance Tax normal-expenditure-out-of-income exemption.

The immediate goal is to import and categorise the 2026/27 activity in the joint bank accounts, Tim's UK bank accounts and Tim's credit cards. This will create an evidence-based pre-retirement expenditure baseline. It must separately identify costs, particularly commuting, that are expected to stop when Tim's consultancy employment ends around April 2027.

This feature is an evidence and reporting ledger. It must not decide that a gift qualifies for the exemption, provide regulated advice, or replace form IHT403, Self Assessment records, executors or professional advisers.

## 2. Confirmed household facts

### People and timing

- Tim, born 13 November 1966, resident in England.
- Wendy, born 23 May 1972, resident in England.
- Tim currently has approximately £120,000 annual PAYE consultancy employment income.
- Tim expects the consultancy work, and therefore employment income and commuting expenditure, to end around April 2027.
- Tim will not start taxable SIPP drawdown before 6 April 2027.
- Wendy will not access pension benefits before her 55th birthday on 23 May 2027.
- Both expect to receive the full new State Pension at their respective State Pension ages.

### Current recurring income baseline

- Wendy's 2025/26 UK property-business profit: approximately £34,000.
- Wendy's share of French property profit: approximately £14,100 gross, with approximately £3,600 French tax and £10,500 received after French tax.
- Wendy's 2025/26 sole-trader profit: approximately £6,500.
- Tim and Wendy each hold approximately £650,000 in ISAs.
- Planning currently assumes a 3.5% natural ISA income yield, but MTD Bookkeeper must record actual receipts rather than this forecast.

### Pension and planned capital flows

- Tim's SIPP: approximately £1.325 million crystallised and £1.402 million uncrystallised.
- Tim has taken tax-free cash only and has not yet triggered the MPAA on the supplied facts.
- The current planning scenario tests £180,000 gross annual taxable SIPP withdrawals from 2027/28.
- Wendy's SIPP is approximately £650,000 and uncrystallised.
- Current planning tests £200,000 annual phased crystallisation, producing up to £50,000 annual PCLS while funds and allowance permit.
- Tim and Wendy intend to subscribe the maximum permitted amount to each ISA annually. The current placeholder is £20,000 each.
- Early ISA contributions are expected to be funded by Wendy's PCLS; later contributions will need to be funded from recurring income, including Tim's SIPP income.
- PCLS and ISA subscriptions are capital movements, not income or normal household expenditure.

### Expenditure and gifts

- Current high-level household expenditure estimate: approximately £75,000 annually.
- Expenditure is considered joint at the planning level.
- Boat berthing and maintenance are substantial components. The boat is legally in Wendy's sole name, but transaction funding and household use—not title alone—must determine the evidence record.
- The eventual gifting plan is expected to use recurring transfers to children, but amounts should be based on demonstrated donor-level income surplus and a safety margin.

## 3. Regulatory/evidential objective

The normal-expenditure-out-of-income exemption requires evidence that each gift:

1. formed part of the donor's normal expenditure;
2. was made from that donor's income; and
3. left that donor able to maintain their normal standard of living from income.

The ledger must therefore be capable of reconstructing an IHT403-style schedule separately for Tim and Wendy. A household aggregate alone is insufficient.

The system must never describe gifts as “IHT exempt” merely because they are regular. Use statuses such as:

- `planned`;
- `recorded`;
- `candidate_normal_expenditure`;
- `evidence_complete`;
- `professional_reviewed`;
- `exception_or_capital_gift`.

## 4. Architectural boundary

### Mandatory separation from MTD tax records

Personal/household records must be logically and visibly separate from the existing UK Property, Foreign Property and Self Employment businesses.

Recommended approach: add a new ledger or book dimension rather than pretending the household is another taxable business.

Suggested books:

- `HOUSEHOLD_IHT` — joint and personal household evidence;
- existing `UK_PROPERTY` — unchanged;
- existing `FOREIGN_PROPERTY` — unchanged;
- existing `SOLE_TRADE` — unchanged.

Requirements:

- household transactions must never enter TaxNav/MTD quarterly summaries or exports;
- business transactions must not be duplicated as household income or expenditure simply because cash later transfers to a personal account;
- the UI must make the active book unmistakable;
- filters, rules, categories and reports should be book-aware;
- permissions, backups, raw imports and audit behaviour can reuse the current infrastructure.

## 5. Accounts in initial scope

Create account records for:

- each joint UK current/savings account used for household income and expenditure;
- each personal UK bank account held by Tim that participates in household flows;
- each of Tim's credit cards;
- optionally Wendy's personal accounts when needed to complete the household evidence chain;
- a manual/external account for gifts or cash movements not present in an imported account.

Each account needs:

- legal owners: Tim, Wendy or joint;
- default donor/expenditure allocation for joint accounts, initially 50/50 unless evidence supports another treatment;
- provider and masked account reference;
- account type: current, savings, credit card, cash, investment cash or other;
- statement parser type and currency;
- opening/closing statement balance where available;
- active dates;
- whether it is included in household evidence, MTD reporting, both or neither.

Do not infer legal ownership from the name used in a bank-description string.

## 6. Credit-card processing requirements

Credit-card purchases must be imported at transaction level. Recording only the monthly direct debit would conceal the underlying nature and date of expenditure.

### Core treatment

- Each purchase is household expenditure on its transaction date and category.
- A payment from a bank account to the credit-card account is an internal transfer, not expenditure.
- Card refunds reverse or reduce the original expenditure category where matched.
- Cash withdrawals and money transfers require review.
- Interest, annual fees, foreign-exchange charges and late fees are separately categorised expenditure.
- Supplementary-card transactions should identify the cardholder where supplied, without assuming that cardholder is necessarily the final donor/expenditure owner.
- Foreign-currency transactions retain original currency, original amount, GBP amount, exchange rate/fee and statement amount where available.

### Reconciliation

For each statement period store:

- opening balance;
- purchases and fees;
- refunds/credits;
- payments;
- closing balance;
- reconciliation difference.

The statement should not be considered complete until the balance reconciliation is within a small rounding tolerance. Missing pages or missing CSV periods must be visible exceptions.

### Duplicate and transfer matching

- Reuse stable transaction hashes and same-file occurrence suffixes.
- Match bank payments to card credits using amount, date window, account pair and reference.
- A matched transfer is excluded from income and expenditure reports at both ends.
- Unmatched apparent card payments remain in review; do not automatically exclude them.
- Avoid netting purchases against the card payment because this destroys category evidence.

### Parser strategy

Implement one parser per actual card statement format, using CSV where available and PDF only where necessary. Preserve every raw row and source file. Parser acceptance fixtures must be based on redacted examples supplied by Tim.

## 7. Transaction classification model

Every household transaction needs two independent classifications:

### Economic class

- `income`;
- `normal_expenditure`;
- `capital_movement`;
- `gift`;
- `internal_transfer`;
- `tax_payment`;
- `refund_or_reversal`;
- `unclassified`.

### Baseline behaviour

- `continues_in_retirement`;
- `ends_on_retirement`;
- `starts_in_retirement`;
- `one_off`;
- `uncertain`.

Additional fields:

- effective start/end date;
- donor/owner allocation between Tim and Wendy;
- normality/evidence status;
- recurring merchant/pattern identifier;
- source transaction and matched transfer/reversal links;
- free-text evidence note;
- manual override and review audit.

This second dimension is essential. Commuting is genuine current expenditure but should be excluded from the forward retirement baseline after the consultancy ends. It must not be deleted or recategorised as non-expenditure.

## 8. Income categories

At minimum support:

- PAYE employment net pay;
- PAYE tax or payroll deductions where available from supporting records;
- taxable SIPP income, gross and PAYE deducted;
- State Pension;
- ISA dividends/distributions;
- ISA interest;
- taxable bank interest;
- UK property net transfers;
- foreign property net transfers;
- sole-trader drawings/transfers;
- refunds and reimbursements;
- other recurring income;
- one-off income/capital receipt;
- unidentified credit.

Important controls:

- A transfer from Wendy's business bank account to a household account is not new economic income if the underlying business profit is already recorded elsewhere. Link it to a source/provenance record rather than double-count it.
- Pension withdrawals must retain gross payment, PAYE tax and net bank receipt when documentation is available.
- ISA income is income in nature even though it is tax-free.
- PCLS is always classified as capital, not income.
- Sale proceeds, account transfers and returned capital must not be treated as income merely because they are credits.

## 9. Normal-expenditure categories

Use IHT403-compatible reporting groups while preserving more useful household subcategories.

### Home and household

- mortgage/rent and secured borrowing;
- council tax;
- gas, electricity, water and other utilities;
- telephone, broadband and media;
- home insurance;
- repairs, maintenance and household services;
- furnishings and household goods;
- groceries and routine household shopping;
- domestic help.

### Personal living

- clothing and personal care;
- medical, dental and care costs;
- subscriptions and memberships;
- leisure and entertainment;
- holidays and travel;
- cash spending pending evidence;
- other routine personal expenditure.

### Transport and work-related personal costs

- vehicles: purchase/lease, fuel, insurance, servicing and tax;
- public transport;
- commuting;
- employment-related meals/accommodation not reimbursed;
- reimbursed work expenditure;
- other transport.

Seed commuting categories with `baseline_behaviour=ends_on_retirement` and an expected end date of 5 April 2027, editable if consultancy timing changes.

### Boat

- berthing/mooring;
- insurance;
- servicing and routine maintenance;
- repairs;
- fuel;
- equipment and upgrades;
- regulatory/licensing costs;
- travel directly associated with the boat;
- capital improvements or boat purchase/sale.

Routine boat costs may form part of normal living expenditure. Purchases, sale proceeds and substantial improvements should be separately classified as capital movements unless reviewed otherwise.

### Tax and professional costs

- UK Income Tax and Self Assessment payments;
- foreign tax;
- financial advice;
- legal/estate-planning fees;
- accountancy fees;
- other professional costs.

Tax must be separately visible because the IHT403 income schedule starts from income after Income Tax.

## 10. Capital-movement categories

- transfers between own/joint bank accounts;
- payments to and from credit cards;
- PCLS receipts;
- ISA subscriptions;
- pension contributions;
- investment purchases and sale proceeds;
- property purchases/sales and capital improvements;
- boat purchase/sale and capital improvements;
- loan principal movements;
- inheritances and other capital receipts;
- capital gifts/PETs;
- unidentified capital movement.

ISA subscriptions must record beneficial owner, tax year and amount so the annual per-person subscription can be reconciled to the planning assumptions without being reported as normal expenditure.

## 11. Gift ledger

Each gift record requires:

- donor: Tim or Wendy;
- recipient and relationship;
- gift date and amount;
- payment account and linked bank transaction;
- payment method/reference;
- gift series/pattern identifier;
- intended frequency and expected duration;
- purpose, if any;
- economic source: current-year income, prior-year accumulated income, capital or uncertain;
- exemption candidate status;
- annual exemption or other exemption allocation, if separately advised;
- supporting-document links;
- review notes and audit history.

Joint-account gifts must never default to an undocumented 50/50 donor split. Require an explicit donor allocation for every gift or gift series, even when a reusable default/pattern is offered.

The gift ledger should reconcile exactly to bank/card transactions. Manual gifts require evidence and review.

## 12. Donor allocation and joint expenditure

The system needs an allocation layer independent of account ownership:

- `Tim 100%`;
- `Wendy 100%`;
- `Joint 50/50`;
- custom percentage split;
- household/common with allocation pending.

Rules may propose an allocation based on account or merchant, but users must be able to override it. The original transaction amount remains unchanged; reporting creates allocated Tim and Wendy shares.

For the baseline, joint expenditure can begin at 50/50. This is a working convention, not a tax conclusion. Boat title in Wendy's name should be recorded as context, while actual funding and consistent household treatment drive allocation.

## 13. Rules and review workflow

Reuse the current first-match-wins rules engine, extended with:

- book scope;
- economic class;
- household category/subcategory;
- baseline behaviour and end date;
- proposed Tim/Wendy allocation;
- transfer-counterparty account;
- gift-series match;
- confidence/review requirement.

Recommended workflow:

1. Import statement.
2. Auto-match internal transfers and card payments.
3. Apply account-scoped categorisation rules.
4. Present unclassified, low-confidence, cash, gift and transfer exceptions.
5. User confirms category, allocation and retirement-baseline behaviour.
6. Optionally create a reusable rule.
7. User marks the transaction reviewed.

Manual overrides must remain protected from later reprocessing, matching existing MTD behaviour.

## 14. Baseline and annual reports

### 2026/27 income and expenditure baseline

Show:

- actual income by source and owner;
- actual expenditure by category and allocated person;
- monthly averages and annual totals;
- recurring versus one-off expenditure;
- expenditure continuing into retirement;
- expenditure ending at retirement, especially commuting;
- new retirement expenditure assumptions;
- reconciled transfers excluded;
- unreviewed/unallocated totals;
- source-account coverage and missing statement periods.

Provide at least three views:

1. **Actual 2026/27:** everything that occurred.
2. **Normal current lifestyle:** actual less exceptional one-offs.
3. **Retirement baseline:** normal lifestyle less `ends_on_retirement`, plus `starts_in_retirement` assumptions.

The report must show the bridge between these figures rather than only the final retirement number.

### Annual IHT403-style evidence pack

Produce separate Tim, Wendy and household reconciliation schedules containing:

- gross income by source;
- Income Tax and foreign tax;
- net income;
- normal expenditure by category;
- retained/saved income, including income-funded ISA subscriptions;
- gifts by recipient and gift series;
- remaining surplus/deficit;
- capital receipts and movements excluded from income;
- exceptions, unclassified items and allocation assumptions;
- links/index to supporting statements and documents.

Exports:

- human-readable PDF;
- detailed CSV;
- executor/adviser evidence ZIP containing reports, source index and immutable annual snapshot metadata.

Reports must say “candidate normal expenditure out of income” rather than assert exemption.

## 15. Data quality and completeness controls

- Statement coverage calendar for every account/card.
- Opening-to-closing balance reconciliation where statements provide balances.
- Duplicate and overlap detection.
- Matched-transfer reconciliation.
- Credit-card payment reconciliation.
- Count/value of unclassified transactions.
- Count/value awaiting Tim/Wendy allocation.
- Gifts without donor, recipient, series or linked transaction.
- Income without source classification.
- PCLS or investment proceeds incorrectly proposed as income.
- Tax-year close blocked or strongly warned while material exceptions remain.
- Locked annual snapshot after review, with later amendments recorded rather than silently replacing it.

## 16. Security, retention and backup

These records contain sensitive personal financial and estate-planning information.

- Continue local-first storage and authenticated access.
- Do not expose the service publicly without the existing access controls.
- Include personal-ledger data, statement files, card statements, evidence attachments and annual snapshots in the existing backup/archive process.
- Mask account/card identifiers in routine UI and logs.
- Never store full card PAN or security codes.
- Preserve source files and raw imported rows according to the same audit principles as MTD business records.
- Logical deletion and audit trails apply to personal transactions, rules, allocations and gifts.

## 17. Suggested delivery slices

### Slice A — personal book and bank baseline

- Add book/ledger separation and `HOUSEHOLD_IHT`.
- Add people and allocation fields.
- Configure joint and Tim bank accounts.
- Reuse/import the applicable bank parsers.
- Add household economic classes, categories and baseline behaviour.
- Import 2026/27 statements and produce initial actual-versus-retirement baseline.

### Slice B — credit cards and transfer reconciliation

- Obtain redacted sample statements for each of Tim's card providers.
- Implement parser fixtures and parsers.
- Add statement balance reconciliation.
- Match bank payments to card credits.
- Categorise card purchases and refunds without double-counting payments.

### Slice C — gifts and IHT evidence

- Add gift series and donor allocation.
- Link gifts to transactions.
- Add income/capital provenance.
- Produce donor-level annual surplus schedules and exception checks.
- Export IHT403-style CSV/PDF evidence.

### Slice D — integration with INCOME planning

- Export reviewed actual baseline figures from MTD Bookkeeper.
- Import or read those figures in INCOME without sharing databases.
- Map continuing/ending/starting expenditure into named INCOME scenarios.
- Compare planned withdrawals/gifts with actual results.
- Avoid bidirectional mutation in the first integration; use versioned exports with source dates.

## 18. Acceptance criteria

The feature is ready for operational use when:

1. Household records cannot appear in MTD/TaxNav submissions.
2. Re-importing overlapping statements does not create duplicates.
3. Two identical genuine same-day transactions remain preserved.
4. Every credit-card statement reconciles from opening to closing balance.
5. Credit-card payments are excluded as transfers while purchases remain expenditure.
6. The 2026/27 baseline reconciles to all in-scope bank and card statements.
7. Commuting appears in actual expenditure and is transparently removed from the retirement baseline after its end date.
8. PCLS and ISA subscriptions appear as capital movements, not normal expenditure or income.
9. Every gift has an explicit donor and linked evidence.
10. Tim and Wendy each have a separate annual income/expenditure/gift/surplus schedule.
11. Unreviewed and unallocated values are prominent and prevent false confidence.
12. Annual snapshots and later amendments are auditable and included in backups.

## 19. Inputs required before implementation

From Tim:

- names/providers and account types for the joint and Tim personal bank accounts;
- one redacted statement sample for each previously unsupported bank format;
- credit-card provider and statement format for each card;
- one complete redacted credit-card statement per distinct format, including summary balances and transaction pages;
- the expected consultancy end date if more precise than April 2027;
- known recurring transfers between the in-scope accounts;
- intended gift recipients and preliminary gift-series structure when available.

Do not request or retain online-banking credentials, full card numbers, CVV codes or unrelated identity documents.

## 20. First implementation recommendation

Begin with Slice A and import from 6 April 2026 onward. Configure commuting as ordinary current expenditure with an expected end date of 5 April 2027. Complete at least one full monthly cycle across all bank accounts before implementing card-payment automation, because the real transfer descriptions will provide reliable matching fixtures.

Once Tim's cards are supported, re-run the entire 2026/27 period so that card purchases replace—not supplement—the previously visible card-payment transfers. Maintain monthly review during the tax year rather than postponing classification until the first gifts are made.
