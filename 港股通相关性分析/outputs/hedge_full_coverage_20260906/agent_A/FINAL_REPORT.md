# Agent A Full Coverage — common engine rerun

## Coverage

- A denominator: 101 funds; processed: 101; mapping rows: 101.
- Unique funds with real common-engine OOS residual rows: 92.
- Unique target runs: 404 (4 horizons × fund/pathway rows); period result rows: 698241.
- Final decisions: `{"INSUFFICIENT_DATA": 8, "MATCH": 45, "NO_MATCH_IN_TESTED_SET": 37, "OUT_OF_SCOPE": 11}`.
- PCF raw-package attempts: 26; complete basket days under strict two-minute stale-age cap: 0.
- Old exploratory window: 20260303–20260803; new confirmation window 20260804–20260904 has 0 claimed unseen OOS days.

## Common engine

- Engine: `FULL237_RHO060_V1`.
- SHA256: `fe73e1368e8b48b276646d0130c662c9de785d6ab306d7f2780d1416d31d8661`.
- Source: `/Users/ellis/工具程序开发/港股通相关性分析/outputs/hedge_full_coverage_20260906/agent_B/scripts/selection_core.py` (read-only B artifact).
- B engine tests: `/Users/ellis/工具程序开发/港股通相关性分析/outputs/hedge_full_coverage_20260906/agent_B/checks/selection_core_tests.json`; all tests PASS in the recorded test artifact.
- The previous provisional A package is preserved under `/Users/ellis/工具程序开发/港股通相关性分析/outputs/hedge_full_coverage_20260906/agent_A/provisional_a_20260906`. Decision changes after common-engine rerun: 55 of 101 assigned funds.

## Interpretation

- `MATCH` requires the own ETF market-price target, same-sample positive OOS correlation ≥0.60 and positive variance reduction under the common engine. These are market-price risk results, not PCF/IOPV basket results.
- `NO_MATCH_IN_TESTED_SET` means common-engine OOS rows existed but the tested eight-tool policy set did not meet the threshold.
- `INSUFFICIENT_DATA` retains concrete candidates and inquiry evidence; no plausible zeros are used.
- Costs, funding, borrow, FX basis, capacity and execution remain UNKNOWN where not evidenced.

See `mapping.json/csv`, `target_results.jsonl`, `candidate_metrics.jsonl`, `provisional_vs_common.jsonl`, `inventory.jsonl`, `fetch_attempts.jsonl`, `checks.jsonl`, `evidence.jsonl`, `RESULTS.xlsx`, `residuals/`, and `weights/`.
