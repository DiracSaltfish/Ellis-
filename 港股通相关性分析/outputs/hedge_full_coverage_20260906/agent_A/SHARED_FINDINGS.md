# Agent A Full Coverage — common engine findings

- A denominator 101 funds; unique common-engine OOS funds 92.
- Final decision counts: {"INSUFFICIENT_DATA": 8, "MATCH": 45, "NO_MATCH_IN_TESTED_SET": 37, "OUT_OF_SCOPE": 11}.
- Common engine `FULL237_RHO060_V1` SHA256 `fe73e1368e8b48b276646d0130c662c9de785d6ab306d7f2780d1416d31d8661`; B tests PASS.
- Target runs 404; period result rows 698241.
- PCF attempts 26; complete strict-stale basket days 0; ETF market-price path remains explicitly distinct from PCF basket.
- Provisional-to-common decision changes: 55; full row-level comparison in `provisional_vs_common.jsonl`.
- New confirmation window 20260804–20260904 is not claimed as unseen OOS.
