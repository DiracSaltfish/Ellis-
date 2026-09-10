# Source and QA notes

Delivery: user-requested local file; portable HTML. Audience: stakeholder. Structure: title → Executive Summary → verified findings/tables/chart → next steps → questions → caveats. Tables chosen for exact multi-day and constituent audit; one chronological chart for synchronized intraday comparison.

Code inspection: read-only local /Users/ellis/newnavnav/internal/valuation/engine.go, estimateFromHoldings (368–448), discovered via graph, no edits. Not a verified production build. Official-labelled output is a holdings-relative estimate, not necessarily raw NAV.

SDK: documentation read, no local config/galaxy_account.ini; historical snapshots limited to verified target tuples, no SDK mutation. Market data fallback to Tencent, with every closing stock amount checked against next-day manager PCF. Minute effective time remains lower confidence than exchange ticks.

IOPV: Tencent qt array position78=1.8252 also matches supplied screenshot; raw SSE select=iopv=1.8300. Field-level timestamp missing. The SSE line probe returned null for requested fields and is not used as historical IOPV. Failed provider probes retained only as access evidence.

Shares: TOT_VOL unit 10,000 shares; check exact STAT_DATE and SEC_CODE after retrieving full response. Net baskets=delta TOT_VOL/50. First sample has no prior baseline in this report.

FX values read from SAFE table; displayed per100 HKD converted to CNY per1 HKD. Roundtrip FX0.855441 and0.855559 are user-screenshot predictions, not independently established final rates.

No test calls to trading endpoints; no messages sent; no deployment or production modifications.
