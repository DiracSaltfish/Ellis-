# Agent B — R1 repair findings

Generated 2026-09-06 (Asia/Shanghai; machine timestamps are UTC).

## Confirmed facts

- `520600.SH` is supported by the official product-facts PDF archived at `data/raw/official/520600_product_facts.pdf` (SHA-256 is recorded in `assets.json` and `evidence.csv`). The PDF identifies the fund, its 2024-12-30 SSE listing, the Hong Kong Connect automobile-theme benchmark, and full-replication strategy.
- The parameterized PCF fetch/parse run retrieved 24/24 exact weekdays from 2026-08-04 through 2026-09-04, with 50 rows per page. The parser only canonicalizes a page when the returned date exactly matches the requested date.
- The repaired IBKR futures manifest contains all 24 weekdays for HSI, HHI, and HTI. The previously missing 2026-08-10, 08-11, and 08-12 dates were re-requested under the shared historical-data lock and merged append-only with the prior 21-date archive.
- All 50 nonzero PCF components have actual SEHK STK 1-minute bars on all 24 requested weekdays after the daily gap repair. `includeExpired=false` is recorded for every STK attempt. The fixed-quantity basket panel has 7,791 common endpoints across 24 dates; each date retains at least 310 common endpoints.
- A pure PCF basket does not require a mainland ETF price. HK-listed 02800/02828 were fetched only as supplemental candidates and are not used in the basket construction.
- The policy lock was written before the new-period panel was read. The fixed HHI+HTI policy is not selected from the new period. New-period Pearson correlation and residual variance reduction are separate fields and separate gates.

## New-period research result

The locked HHI+HTI futures policy has 24 valid new OOS days and passes the requested primary gates at 5/15/30/60 minutes:

| Horizon | Pearson OOS | 95% day-block CI | Variance reduction | Status |
|---:|---:|---:|---:|---|
| 5m | 0.6409 | [0.5983, 0.6805] | 40.50% | CONFIRMED |
| 15m | 0.7337 | [0.6923, 0.7675] | 53.17% | CONFIRMED |
| 30m | 0.7400 | [0.6880, 0.7842] | 54.73% | CONFIRMED |
| 60m | 0.7188 | [0.6502, 0.7817] | 51.57% | CONFIRMED |

The main 30-minute price-proxy result is therefore suitable as a conditional price proxy. Execution remains conditional because historical fills, fees, borrow, and funding costs were not verified.

## Independent source limitation

The copied remote Hong Kong tick archive contains 15 observed dates (08-04 through 08-25, with 08-21 absent) and no copied per-stock archive for 08-26 through 09-04. It was aggregated to minute closes as a cross-check only; it was not used to fill IBKR gaps. The research panel uses the complete repaired IBKR STK/futures inputs.

## Known residual gaps

- Official per-security corporate-action/suspension review is not closed for all 50 PCF codes. Five pre-existing official event records are preserved as partial evidence; other codes are explicitly marked `REVIEW_NOT_COMPLETE`.
- HK-listed ETF supplement 02800/02828 has 22/24-day coverage and is not required for the pure PCF basket.
- The other 80 assigned funds were not claimed as technically confirmed in this B slice. Their rows remain explicit `INSUFFICIENT_EVIDENCE` / `NOT_RUN` baselines, rather than inheriting 520600's evidence.
