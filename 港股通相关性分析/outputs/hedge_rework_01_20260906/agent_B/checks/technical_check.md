# B R1 technical checks

- PASS `denominator_81`: actual=81; expected=81 
- PASS `fund_ids_unique`: actual=81; expected=81 
- PASS `no_hk_single_stock_hedge`: actual=['ETF', '期货']; expected=['ETF', '期货'] 
- PASS `new_metric_rows`: actual=4; expected=4 
- PASS `new_metric_rho_gate`: actual=[0.6408982345, 0.7337057203, 0.7400177498, 0.7188072798]; expected=>=0.60 
- PASS `new_metric_vr_gate`: actual=[0.4049573293, 0.5317443487, 0.5472899034, 0.5156735791]; expected=>0 
- PASS `new_oos_days`: actual=[24]; expected=[24] 
- PASS `panel_rows_unique`: actual=7791; expected=7791 duplicates=0
- PASS `pcf_exact_dates`: actual=24; expected=24 
- PASS `ibkr_manifest_dates`: actual=24; expected=24 
- PASS `lock_precedes_analysis`: actual=['2026-09-06T13:36:01.941131+00:00', '2026-09-06T13:38:57.783190+00:00']; expected=lock < summary 
- PASS `evidence_paths_exist`: actual=[]; expected=[] 
- PASS `evidence_hashes_match`: actual=[]; expected=[] 
- PASS `actual_task_timestamps`: actual=7; expected=7 
