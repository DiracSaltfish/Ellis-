#!/usr/bin/env python3
"""Run the R1 Pearson-correlation analysis for 520600.

Old-period scores are exploratory comparators.  The new-period policy is
locked from the old run before reading its new-period scores.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import itertools
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OLD_LABELS = ROOT / "data/repro_old/normalized"
PANEL = ROOT / "data/raw/new_period_520600/520600_basket_panel_1m.jsonl.gz"
STK = ROOT / "data/raw/new_period_520600/520600_stk_1m.jsonl.gz"
OUT = ROOT / "data/raw/new_period_520600"
HORIZONS = [5, 15, 30, 60]
HK = ZoneInfo("Asia/Hong_Kong")
OLD_TO_NEW = {"HSI_FUT": "HSI_U6", "HHI_FUT": "HHI_U6", "HTI_FUT": "HTI_U6", "02800": "02800", "02828": "02828"}
FAMILIES = {"HSI_FUT": "HSI", "HHI_FUT": "HHI", "HTI_FUT": "HSTECH", "02800": "HSI", "02828": "HHI"}
FIXED_BETA = {5: {"HHI_FUT": 0.4065644528940298, "HTI_FUT": 0.3588191301867448}, 15: {"HHI_FUT": 0.4338586641256397, "HTI_FUT": 0.4452898189379947}, 30: {"HHI_FUT": 0.5092797293653971, "HTI_FUT": 0.4490979543084183}, 60: {"HHI_FUT": 0.7152087030417262, "HTI_FUT": 0.3604268558717037}}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""): h.update(block)
    return h.hexdigest()


def corr(a, b):
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0: return None
    return float(np.corrcoef(a, b)[0, 1])


def metric(y, hedge):
    y, hedge = np.asarray(y, float), np.asarray(hedge, float); residual = y - hedge
    if len(y) < 2: return {"sample_count": int(len(y)), "correlation": None, "residual": residual.tolist()}
    return {"sample_count": int(len(y)), "correlation": corr(y, hedge), "target_std_bp": float(np.std(y, ddof=1) * 10000), "residual_std_bp": float(np.std(residual, ddof=1) * 10000), "variance_reduction": float(1 - np.var(residual, ddof=1) / np.var(y, ddof=1)) if np.var(y, ddof=1) else None, "residual_mean_bp": float(np.mean(residual) * 10000), "up_es95_bp": float(np.sort(residual)[-max(1, int(np.ceil(len(residual)*.05))):].mean() * 10000), "down_es95_bp": float(np.sort(-residual)[-max(1, int(np.ceil(len(residual)*.05))):].mean() * 10000), "residual": residual.tolist()}


def block_corr_ci(df, y_col, h_col, reps=2000, seed=520600):
    if df.empty: return [None, None]
    days = sorted(df["date"].unique()); rng = np.random.default_rng(seed); vals = []
    for _ in range(reps):
        sampled = rng.choice(days, size=len(days), replace=True)
        sub = pd.concat([df[df.date == d] for d in sampled], ignore_index=True)
        v = corr(sub[y_col].to_numpy(), sub[h_col].to_numpy())
        if v is not None: vals.append(v)
    return [float(np.quantile(vals, .025)), float(np.quantile(vals, .975))] if vals else [None, None]


def old_comparators(h):
    df = pd.read_parquet(OLD_LABELS / f"labels_{h}m.parquet")
    df = df[(df.date >= "20260610") & (df.date <= "20260803")].copy()
    rows = []
    tools = [x for x in OLD_TO_NEW if x in df]
    for tool in tools:
        m = metric(df.y, df[tool]); m.update({"policy_id": f"OLD_SINGLE_{tool}_{h}M", "tools": [tool], "selection_rule": "exploratory old OOS comparator; not a new-period lock"})
        rows.append(m)
    for a, b in itertools.combinations(tools, 2):
        if FAMILIES.get(a) == FAMILIES.get(b): continue
        x = df[[a, b]].to_numpy(float); beta = np.linalg.lstsq(x, df.y.to_numpy(float), rcond=None)[0]; proxy = x @ beta
        m = metric(df.y, proxy); m.update({"policy_id": f"OLD_PAIR_{a}_{b}_{h}M", "tools": [a, b], "beta": {a: float(beta[0]), b: float(beta[1])}, "selection_rule": "exploratory old OOS comparator; OLS descriptive only"}); rows.append(m)
    return df, rows


def load_new_panel():
    rows = [json.loads(x) for x in gzip.open(PANEL, "rt", encoding="utf-8")]
    df = pd.DataFrame(rows)
    if df.empty: return df
    etf = {"02800": {}, "02828": {}}
    if STK.exists():
        for line in gzip.open(STK, "rt", encoding="utf-8"):
            rec = json.loads(line); code = rec.get("security_id")
            if code not in etf: continue
            for bar in rec.get("bars", []):
                stamp = datetime.fromtimestamp(int(bar["date"]), tz=timezone.utc).astimezone(HK).strftime("%Y%m%d %H:%M")
                etf[code][stamp] = float(bar["close"])
    for code, vals in etf.items():
        if not vals: continue
        df[f"_{code}_price"] = [vals.get(f"{r.date} {int(r.minute)//60:02d}:{int(r.minute)%60:02d}") for r in df.itertuples()]
    return df


def make_new_labels(panel, h):
    if panel.empty: return pd.DataFrame()
    rows = []
    for day, group in panel.groupby("date", sort=True):
        g = group.sort_values("minute").set_index("minute")
        for minute in g.index:
            end = int(minute) + h
            if end not in g.index or int(minute) < 570 or (720 <= int(minute) < 780) or (720 <= end < 780): continue
            a, b = g.loc[minute], g.loc[end]
            y = b.basket_hkd / a.basket_hkd - 1
            out = {"date": day, "minute": int(minute), "y": float(y)}
            for new_name, old_name in OLD_TO_NEW.items():
                if new_name in {"HSI_FUT", "HHI_FUT", "HTI_FUT"}:
                    col = old_name
                    out[new_name] = float(b[col] / a[col] - 1)
                else:
                    pcol = f"_{new_name}_price"
                    if pcol in g and pd.notna(a.get(pcol)) and pd.notna(b.get(pcol)): out[new_name] = float(b[pcol] / a[pcol] - 1)
            if all(k in out for k in ("HHI_FUT", "HTI_FUT")): rows.append(out)
    return pd.DataFrame(rows)


def main():
    # Create the policy lock before reading any new-period panel values.  The
    # lock is therefore an auditable pre-analysis artifact, not a post-hoc label.
    lock_path = OUT / "520600_selection_lock.json"
    if not lock_path.exists():
        pre_lock = {"fund_id": "520600.SH", "status": "LOCKED_BEFORE_NEW_PERIOD", "locked_at_utc": datetime.now(timezone.utc).isoformat(), "policy_rule": "old-window fixed pair; no new-period model selection", "policies": {str(h): {"policy_id": f"P520600_R1_FIXED_HHI_HTI_{h}M", "tools": ["HHI_FUT", "HTI_FUT"], "beta": FIXED_BETA[h]} for h in HORIZONS}, "criteria_version": "USER_RHO_060_FUTURES_ETF"}
        lock_path.write_text(json.dumps(pre_lock, ensure_ascii=False, indent=2), encoding="utf-8")
    panel = load_new_panel(); all_metrics = []; comparators = []; sensitivity = []; residual_rows = []; daily_weights = []
    for h in HORIZONS:
        old_df, old_rows = old_comparators(h)
        for r in old_rows: r.update({"horizon_min": h, "sample_status": "REUSED_OR_UNPROVEN", "sample_start": str(old_df.date.min()) if not old_df.empty else None, "sample_end": str(old_df.date.max()) if not old_df.empty else None, "source": "old exploratory OOS"}); comparators.append(r)
        new = make_new_labels(panel, h)
        beta = FIXED_BETA[h]
        if not new.empty:
            new["locked_proxy"] = beta["HHI_FUT"] * new["HHI_FUT"] + beta["HTI_FUT"] * new["HTI_FUT"]
            m = metric(new.y, new.locked_proxy); status = "CONFIRMED" if int(new.date.nunique()) >= 20 and (m.get("correlation") or -1) >= 0.60 and (m.get("variance_reduction") or -1) > 0 else "EXPLORATORY"; m.update({"run_id": "B-520600-NEW-1M-LOCKED", "fund_id": "520600.SH", "horizon_min": h, "policy_id": f"P520600_R1_FIXED_HHI_HTI_{h}M", "model_id": "HHI_HTI_fixed_pair", "sample_status": status, "sample_start": str(new.date.min()), "sample_end": str(new.date.max()), "oos_days": int(new.date.nunique()), "correlation_ci_low": block_corr_ci(new, "y", "locked_proxy")[0], "correlation_ci_high": block_corr_ci(new, "y", "locked_proxy")[1], "correlation_method": "Pearson OOS with day-block bootstrap", "correlation_threshold": 0.60, "criteria_version": "USER_RHO_060_FUTURES_ETF", "beta": beta, "source_path": str(PANEL.relative_to(ROOT.parent.parent))})
            m.pop("residual", None); all_metrics.append(m)
            residual = new.y - new.locked_proxy
            for i, r in new.assign(residual=residual).iterrows(): residual_rows.append({"fund_id": "520600.SH", "horizon_min": h, "date": r.date, "minute": int(r.minute), "y": float(r.y), "locked_proxy": float(r.locked_proxy), "residual": float(r.residual)})
            for scale in (0.8, 1.0, 1.2):
                proxy = scale * new.locked_proxy; mm = metric(new.y, proxy); sensitivity.append({"fund_id": "520600.SH", "horizon_min": h, "policy_id": f"P520600_R1_FIXED_HHI_HTI_{h}M", "scenario_id": f"beta_scale_{scale:.1f}", "refit": False, "sample_status": "EXPLORATORY", "sample_hash": sha(PANEL), "oos_days": int(new.date.nunique()), "hedge_return_correlation": mm.get("correlation"), "correlation_ci_low": None, "correlation_ci_high": None, "variance_reduction": mm.get("variance_reduction"), "residual_std_bp": mm.get("residual_std_bp"), "pass": bool(mm.get("correlation") is not None and mm.get("correlation") >= .6 and (mm.get("variance_reduction") or -1) > 0), "explanation": "新期已锁定HHI+HTI的倍数诊断；未用新期重新选模。"})
        daily_weights.append({"fund_id": "520600.SH", "horizon_min": h, "effective_date": "2026-08-03", "policy_id": f"P520600_R1_FIXED_HHI_HTI_{h}M", "train_end": "2026-08-03", "HHI_FUT_beta": beta["HHI_FUT"], "HTI_FUT_beta": beta["HTI_FUT"], "selection_reason": "旧样本滚动研究锁定；新期只评估，不选模。"})
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(comparators).to_csv(OUT / "520600_exploratory_comparators.csv", index=False)
    pd.DataFrame(all_metrics).to_json(OUT / "520600_new_period_metrics.json", orient="records", force_ascii=False, indent=2)
    pd.DataFrame(sensitivity).to_json(OUT / "520600_new_period_sensitivity.json", orient="records", force_ascii=False, indent=2)
    pd.DataFrame(daily_weights).to_csv(OUT / "520600_r1_daily_weights.csv", index=False)
    with gzip.open(OUT / "520600_new_period_residuals.jsonl.gz", "wt", encoding="utf-8") as f:
        for r in residual_rows: f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    lock = json.loads(lock_path.read_text(encoding="utf-8")); lock["new_period_input_hash"] = sha(PANEL)
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "new_panel_rows": int(len(panel)), "new_dates": sorted(panel.date.unique()) if not panel.empty else [], "metrics": all_metrics, "old_comparator_rows": len(comparators), "sensitivity_rows": len(sensitivity), "policy_lock": lock}
    (OUT / "520600_r1_analysis_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
