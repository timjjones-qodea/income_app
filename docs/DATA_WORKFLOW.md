# Data workflow

This app is designed around observable portfolio facts, not guessed portfolio state.

The source of truth for holdings is AJ Bell. The source of truth for actual income paid is the AJ Bell cash statement. The AIC Income Builder export is an optional planning layer for estimating repeatable investment trust income.

## The core question

The app should help answer:

- What do Tim and Wendy currently hold?
- What natural dividend and interest income did those holdings actually produce?
- Which account wrapper did each receipt land in?
- Which income is tax-free, pension-wrapped or taxable?
- What income looks repeatable, rather than inflated by special dividends?
- What does the combined household income projection look like before drawing capital?

## Import sequence

Use this sequence for every account.

### 1. AJ Bell portfolio CSV

Required first.

This creates:

- current security list;
- tickers where AJ Bell supplies them;
- current quantities;
- current market values;
- latest holding snapshot per account/security.

The Holdings page and Dashboard portfolio value depend on this file.

### 2. AJ Bell cash statement CSV

Required for actual income.

This creates:

- dividend transactions;
- interest transactions;
- charges and other cash movements for audit context;
- payment-time share quantities where AJ Bell includes the quantity in the dividend description;
- per-share actual dividend information.

The Income page depends on this file. The app intentionally rejects an AJ Bell cash statement until the matching account has a committed AJ Bell portfolio import, because dividend descriptions are matched to the securities created from holdings.

Built-in AJ Bell cash-statement rules:

| Description pattern | Code | Treatment |
| --- | --- | --- |
| `BALANCE B/F *` | `OPENING_BALANCE` | Opening balance brought forward. Kept for audit context; not treated as income, charge or security movement. |
| `Account charge for shares - <month> - <account code>` | `ACCOUNT_CHARGE` | Account charge. The AJ Bell account code, for example `ABWD2VD`, is extracted for cash-activity reporting. |
| `Cash Withdrawal` | `CASH_WITHDRAWAL` | Cash drawing/withdrawal. Reportable separately from dividend and interest income. |
| `Gross interest to <date>` | `GROSS_INTEREST` | Gross cash interest. Included in Income as interest. |
| `Dividend <quantity> <security name>` | `DIVIDEND` | Matched to the imported holding/security where possible. |

Only security-bearing rows such as dividends, buys and sells are shown on Securities as unmatched. Opening balances, account charges, cash withdrawals and gross interest are normal cash-ledger rows and do not need manual security mapping.

### 3. AIC Income Builder export

Optional but useful for investment trusts.

This creates:

- trailing income received by AIC portfolio/security;
- AIC sector;
- dividend frequency;
- trailing yield;
- a planning baseline that usually excludes the “special dividend as repeatable income” problem.

The AIC export is especially useful for the non-VCT investment trust portfolios. For Tim’s VCT GIA, the AJ Bell cash statement may show the full actual tax-free income, but AIC can still be useful if you want a conservative regular-income baseline.

## How planning income is selected

For each current holding, planning income uses the highest-priority available source:

1. Manual annual dividend per share.
2. Manual forward yield.
3. AIC portfolio income snapshot.
4. AJ Bell actual trailing-12-month dividend receipts, including specials.
5. Imported dividend events.
6. Asset-type fallback yield.

In practice:

- Use AJ Bell cash statements to understand what was really paid.
- Use AIC exports to avoid over-projecting one-off specials.
- Use manual assumptions where a holding has changed materially, the income policy changed, or the data does not represent the future.

## Account and tax treatment model

The app currently seeds six accounts:

| Account | Owner | Wrapper | Current interpretation |
| --- | --- | --- | --- |
| Tim ISA | Tim | ISA | tax-free dividend/interest income |
| Tim SIPP | Tim | SIPP | pension-wrapped income; taxable only when withdrawn |
| Tim GIA | Tim | GIA | VCT-only; dividends treated as tax-free |
| Wendy ISA | Wendy | ISA | tax-free dividend/interest income |
| Wendy SIPP | Wendy | SIPP | pension-wrapped income; taxable only when withdrawn |
| Wendy GIA | Wendy | GIA | unwrapped taxable investment income |

Current tax treatment is displayed as guidance. The app does not yet calculate tax due, allowance usage, pension withdrawals, or dividend allowance effects.

## Data quality checks

After each import:

- Check the import detail page for validation errors and warnings.
- Check Securities for unmatched transaction descriptions.
- Check the cash activity export for charges, withdrawals, opening balances and gross interest.
- Check Income for missing expected dividends.
- Check Holdings for implausible fallback-yield rows.
- Prefer manual assumptions for securities where the calculated source says fallback yield.

If a portfolio CSV was loaded against the wrong account, open Holdings, expand the affected account row and use **Delete holdings for this account**. This removes holding snapshots for that account only. It does not delete AJ Bell cash-statement transactions, income history, AIC planning snapshots or securities.

## Refresh cadence

Suggested cadence for an acquire-and-hold retirement income portfolio:

- Monthly or after trades: AJ Bell portfolio CSV.
- Monthly or quarterly: AJ Bell cash statement.
- Monthly or quarterly: AIC Income Builder export for covered investment-trust portfolios.
- Annually: archive/export reports for tax-year review.

## Known data limitations

- AJ Bell cash-statement dividend matching relies on security names in dividend descriptions.
- AIC Income Builder is manually downloaded; there is no approved automated ingestion.
- AIC exports provide portfolio-level trailing income, not a complete tax model.
- Special dividends can materially distort actual trailing income.
- Holdings are snapshots, not a full transaction-derived position ledger.
- No corporate action handling exists yet.
