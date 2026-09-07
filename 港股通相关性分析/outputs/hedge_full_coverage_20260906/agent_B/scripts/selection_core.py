"""Deterministic, data-independent hedge-selection engine for FULL237_RHO060_V1.

Input panels use one row per minute-end observation with ``timestamp_end``,
``target_price`` and candidate price columns.  The engine never fills missing
prices, never takes absolute correlation, and enforces non-negative beta,
per-leg beta <= 2 and total beta <= 2 with a constrained optimizer.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import time
from itertools import combinations, product
from typing import Iterable, Optional, Sequence

import numpy as np
import pandas as pd

ENGINE_VERSION = "FULL237_RHO060_V1"
CORE_CANDIDATES = ["HSI_FUT", "HHI_FUT", "HTI_FUT", "02800", "02828"]
HORIZONS = (5, 15, 30, 60)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def sample_hash(frame: pd.DataFrame, cols: Sequence[str]) -> str:
    base = [c for c in ("timestamp_end", "date", "minute_end") if c in frame.columns]
    x = frame[[*base, *cols]].copy()
    if not base:
        x.insert(0, "row_id", np.arange(len(x)))
    payload = x.to_csv(index=False, date_format="%Y-%m-%dT%H:%M:%S").encode()
    return hashlib.sha256(payload).hexdigest()


def _same_continuous_session(start_minute: int, end_minute: int) -> bool:
    def session(m):
        if 570 <= m <= 690:
            return "AM"
        if 780 <= m <= 900:
            return "PM"
        return None
    return session(start_minute) is not None and session(start_minute) == session(end_minute)


def make_returns(panel: pd.DataFrame, horizon_min: int) -> pd.DataFrame:
    """Create same-day, same-session forward returns at minute-end labels."""
    if horizon_min not in HORIZONS:
        raise ValueError(f"unsupported horizon {horizon_min}")
    df = panel.copy()
    if "timestamp_end" not in df:
        raise ValueError("panel requires timestamp_end")
    df["timestamp_end"] = pd.to_datetime(df["timestamp_end"], utc=True)
    df = df.sort_values("timestamp_end").drop_duplicates("timestamp_end", keep="last")
    df["date"] = df["timestamp_end"].dt.tz_convert("Asia/Hong_Kong").dt.strftime("%Y%m%d")
    df["minute_end"] = df["timestamp_end"].dt.tz_convert("Asia/Hong_Kong").dt.hour * 60 + df["timestamp_end"].dt.tz_convert("Asia/Hong_Kong").dt.minute
    df = df[df["minute_end"].between(570, 900) & ~df["minute_end"].between(691, 779)].copy()
    price_cols = [c for c in df.columns if c not in {"timestamp_end", "date", "minute_end"}]
    records = df.to_dict("records")
    by_key = {(r["date"], int(r["minute_end"])): r for r in records}
    rows = []
    for row in records:
        end = int(row["minute_end"]) + horizon_min
        if not _same_continuous_session(int(row["minute_end"]), end):
            continue
        other = by_key.get((row["date"], end))
        if other is None:
            continue
        out = {"timestamp_end": row["timestamp_end"], "date": row["date"], "minute_end": int(row["minute_end"]), "horizon_min": horizon_min}
        for c in price_cols:
            a, b = row.get(c), other.get(c)
            try:
                out[c] = float(b) / float(a) - 1.0 if pd.notna(a) and pd.notna(b) and float(a) != 0 else np.nan
            except (TypeError, ValueError):
                out[c] = np.nan
        rows.append(out)
    return pd.DataFrame(rows)


def fit_beta_constrained(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Solve min ||y-Xb||² with 0<=b_i<=2 and sum(b)<=2.

    The runtime bundle does not ship scipy.  Since policy size is capped at
    three legs, enumerate the faces of the small feasible polytope and solve
    each least-squares/KKT system with NumPy.  This is deterministic and
    equivalent to the convex constrained optimum within numerical tolerance.
    """
    x = np.asarray(x, dtype=float); y = np.asarray(y, dtype=float)
    if x.ndim != 2 or x.shape[1] == 0 or len(x) < x.shape[1] + 1:
        return np.zeros(x.shape[1] if x.ndim == 2 else 0)
    p = x.shape[1]
    if p > 3:
        raise ValueError("selection_core supports at most three legs per policy")
    if p == 1:
        denom = float(np.dot(x[:, 0], x[:, 0]))
        return np.array([np.clip(float(np.dot(x[:, 0], y)) / denom, 0.0, 2.0) if denom else 0.0])
    if p == 2:
        # Fast exact active-set path for all production policies (singles and
        # pairs); the general face enumeration below remains the guardrail for
        # the unit-testable three-leg interface.
        candidates = [np.linalg.lstsq(x, y, rcond=None)[0],
                      np.array([0.0, np.dot(x[:, 1], y) / np.dot(x[:, 1], x[:, 1]) if np.dot(x[:, 1], x[:, 1]) else 0.0]),
                      np.array([2.0, np.dot(x[:, 1], y - 2.0*x[:, 0]) / np.dot(x[:, 1], x[:, 1]) if np.dot(x[:, 1], x[:, 1]) else 0.0]),
                      np.array([np.dot(x[:, 0], y) / np.dot(x[:, 0], x[:, 0]) if np.dot(x[:, 0], x[:, 0]) else 0.0, 0.0]),
                      np.array([np.dot(x[:, 0], y - 2.0*x[:, 1]) / np.dot(x[:, 0], x[:, 0]) if np.dot(x[:, 0], x[:, 0]) else 0.0, 2.0]),
                      np.array([0.0, 2.0]), np.array([2.0, 0.0]), np.array([0.0, 0.0])]
        a = x[:, 0] - x[:, 1]
        denom = float(np.dot(a, a))
        z = float(np.dot(a, y - 2.0*x[:, 1]) / denom) if denom else 1.0
        candidates.append(np.array([z, 2.0-z]))
        feasible = []
        for b in candidates:
            b = np.asarray(b, dtype=float)
            if np.all(b >= -1e-8) and np.all(b <= 2.0+1e-8) and b.sum() <= 2.0+1e-8:
                feasible.append(np.clip(b, 0.0, 2.0))
        if feasible:
            return min(feasible, key=lambda b: float(np.sum((y - x @ b) ** 2)))
    best: Optional[np.ndarray] = None
    best_sse = float("inf")
    for states in product(("free", "lo", "hi"), repeat=p):
        fixed = {i: (0.0 if s == "lo" else 2.0) for i, s in enumerate(states) if s != "free"}
        free = [i for i, s in enumerate(states) if s == "free"]
        for budget_active in (False, True):
            remaining = 2.0 - sum(fixed.values())
            if budget_active and remaining < -1e-9:
                continue
            b = np.zeros(p, dtype=float)
            for i, v in fixed.items():
                b[i] = v
            if not free:
                if budget_active and abs(remaining) > 1e-8:
                    continue
            else:
                rhs = y - (x[:, list(fixed)] @ b[list(fixed)] if fixed else 0.0)
                a = x[:, free]
                if budget_active:
                    c = np.ones((1, len(free)), dtype=float)
                    gram = a.T @ a
                    kkt = np.block([[gram, c.T], [c, np.zeros((1, 1))]])
                    krhs = np.concatenate([a.T @ rhs, np.array([remaining])])
                    try:
                        z = np.linalg.solve(kkt, krhs)[:len(free)]
                    except np.linalg.LinAlgError:
                        z = np.linalg.lstsq(kkt, krhs, rcond=None)[0][:len(free)]
                else:
                    z = np.linalg.lstsq(a, rhs, rcond=None)[0]
                b[free] = z
            if np.any(b < -1e-8) or np.any(b > 2.0 + 1e-8) or np.sum(b) > 2.0 + 1e-8:
                continue
            b = np.clip(b, 0.0, 2.0)
            if budget_active and b.sum() and abs(b.sum() - 2.0) > 1e-7:
                b *= 2.0 / b.sum()
            sse = float(np.sum((y - x @ b) ** 2))
            if sse < best_sse - 1e-12:
                best, best_sse = b, sse
    return best if best is not None else np.zeros(p, dtype=float)


def _corr(y, h):
    y = np.asarray(y, dtype=float); h = np.asarray(h, dtype=float)
    if len(y) < 3 or np.std(y) == 0 or np.std(h) == 0:
        return None
    return float(np.corrcoef(y, h)[0, 1])


def block_corr_ci(y, h, dates, reps=500, seed=20260906):
    days = np.array(sorted(set(map(str, dates))))
    if len(days) < 2:
        return [None, None]
    rng = np.random.default_rng(seed); vals = []
    y = np.asarray(y); h = np.asarray(h); dates = np.asarray(dates)
    for _ in range(reps):
        sampled = rng.choice(days, len(days), replace=True)
        idx = np.concatenate([np.flatnonzero(dates == d) for d in sampled])
        c = _corr(y[idx], h[idx])
        if c is not None:
            vals.append(c)
    return [float(np.quantile(vals, .025)), float(np.quantile(vals, .975))] if vals else [None, None]


def metric(y, hedge, dates, ci=False):
    y = np.asarray(y, dtype=float); hedge = np.asarray(hedge, dtype=float)
    residual = y - hedge
    var_y = float(np.var(y, ddof=1)) if len(y) > 1 else None
    var_r = float(np.var(residual, ddof=1)) if len(y) > 1 else None
    tail_n = max(1, int(np.ceil(len(residual) * .05))) if len(residual) else 0
    out = {"rows": int(len(y)), "days": int(len(set(map(str, dates)))), "correlation": _corr(y, hedge), "target_std_bp": float(np.std(y, ddof=1) * 10000) if len(y) > 1 else None, "residual_std_bp": float(np.std(residual, ddof=1) * 10000) if len(y) > 1 else None, "variance_reduction": (1 - var_r / var_y) if var_y else None, "residual_mean_bp": float(np.mean(residual) * 10000) if len(y) else None, "up_es95_bp": float(np.sort(residual)[-tail_n:].mean() * 10000) if tail_n else None, "down_es95_bp": float(np.sort(-residual)[-tail_n:].mean() * 10000) if tail_n else None}
    if ci:
        out["correlation_ci_low"], out["correlation_ci_high"] = block_corr_ci(y, hedge, dates)
    else:
        out["correlation_ci_low"] = out["correlation_ci_high"] = None
    return out


def policy_list(candidate_cols: Sequence[str], families: dict[str, str] | None = None):
    """Return economically simple policies: all singles + cross-family pairs."""
    families = families or {}
    policies = [[c] for c in candidate_cols]
    for a, b in combinations(candidate_cols, 2):
        if families.get(a) and families.get(a) == families.get(b):
            continue
        policies.append([a, b])
    return policies


def _usable(frame, cols):
    x = frame[["date", *cols, "target"]].dropna()
    return x


def descriptive_comparison(ret: pd.DataFrame, policies: Sequence[Sequence[str]], target_col="target", ci=True):
    rows = []
    for legs in policies:
        u = ret[["date", target_col, *legs]].dropna()
        if len(u) < 3:
            rows.append({"policy_id": "+".join(legs), "tools": list(legs), "status": "UNAVAILABLE", "sample_group_id": f"DESC_{'+'.join(legs)}", "sample_hash": sample_hash(u.rename(columns={target_col: "target_price"}), [target_col, *legs]) if len(u) else None})
            continue
        beta = fit_beta_constrained(u[legs].to_numpy(), u[target_col].to_numpy())
        m = metric(u[target_col], u[legs].to_numpy() @ beta, u["date"], ci=ci)
        rows.append({"policy_id": "+".join(legs), "tools": list(legs), "status": "SHORT_SAMPLE", "sample_group_id": f"DESC_{'+'.join(legs)}", "beta": {c: float(v) for c, v in zip(legs, beta)}, "sample_hash": sample_hash(u.rename(columns={target_col: "target_price"}), [target_col, *legs]), **m})
    return rows


def rolling_oos(panel: pd.DataFrame, candidate_cols: Sequence[str], horizons: Iterable[int] = HORIZONS, policies: Sequence[Sequence[str]] | None = None, min_train_days=60, fit_days=50, validation_days=10):
    """Run rolling 50-fit/10-validation/60-refit/next-day OOS evaluation."""
    policies = policies or policy_list(candidate_cols)
    all_results = {}; all_residuals = {}; all_weights = {}
    for horizon in horizons:
        ret = make_returns(panel, horizon)
        # make_returns preserves target_price as the target return column.
        if "target_price" not in ret:
            raise ValueError("panel requires target_price")
        ret = ret.rename(columns={"target_price": "target"})
        dates = sorted(ret["date"].dropna().unique())
        oos_rows=[]; daily_weights=[]; validation_rows=[]; validation_samples=[]
        for i, test_day in enumerate(dates):
            prior = dates[:i]
            if len(prior) < min_train_days:
                continue
            hist = ret[ret["date"].isin(prior[-min_train_days:])]
            hist_dates = sorted(hist["date"].unique())
            if len(hist_dates) < min_train_days:
                continue
            fit_set = hist[hist["date"].isin(hist_dates[:fit_days])]
            val_set = hist[hist["date"].isin(hist_dates[fit_days:fit_days+validation_days])]
            policy_val=[]
            for legs in policies:
                f = fit_set[["date", "target", *legs]].dropna(); v = val_set[["date", "target", *legs]].dropna()
                if len(f) < 3 or len(v) < 3:
                    continue
                beta_fit = fit_beta_constrained(f[legs].to_numpy(), f["target"].to_numpy())
                vm = metric(v["target"], v[legs].to_numpy() @ beta_fit, v["date"], ci=False)
                policy_val.append({"policy_id": "+".join(legs), "tools": list(legs), "beta_fit": beta_fit, **vm})
                validation_samples.extend({"policy_id":"+".join(legs),"date":str(d),"target":float(y),"proxy":float(h)} for d,y,h in zip(v["date"],v["target"],v[legs].to_numpy() @ beta_fit))
            if not policy_val:
                continue
            # Single-leg pass is preferred. Otherwise choose highest positive
            # validation correlation/VR score, with fewer legs as tie-breaker.
            singles = [x for x in policy_val if len(x["tools"]) == 1 and (x["correlation"] or -1) >= .60 and (x["variance_reduction"] or -1) > 0]
            pool = singles or policy_val
            chosen = sorted(pool, key=lambda x: ((x["correlation"] if x["correlation"] is not None else -9), (x["variance_reduction"] if x["variance_reduction"] is not None else -9), -len(x["tools"]), x["policy_id"]), reverse=True)[0]
            legs=chosen["tools"]
            refit = hist[["date", "target", *legs]].dropna()
            beta = fit_beta_constrained(refit[legs].to_numpy(), refit["target"].to_numpy())
            test = ret[(ret["date"] == test_day)][["timestamp_end", "date", "minute_end", "target", *legs]].dropna()
            if len(test) < 3:
                continue
            test_proxy = test[legs].to_numpy() @ beta
            for row, proxy in zip(test.to_dict("records"), test_proxy):
                row.update({"horizon_min": horizon, "policy_id": "+".join(legs), "proxy_return": float(proxy), "residual": float(row["target"] - proxy), "beta": {c: float(v) for c, v in zip(legs, beta)}}); oos_rows.append(row)
            daily_weights.append({"date":test_day,"horizon_min":horizon,"policy_id":"+".join(legs),"tools":legs,"beta":{c:float(v) for c,v in zip(legs,beta)},"fit_dates":[hist_dates[0],hist_dates[-1]],"validation_dates":[hist_dates[fit_days],hist_dates[-1]],"selected_on":"validation_only"})
            for p in policy_val:
                validation_rows.append({"test_day":test_day,"horizon_min":horizon,"policy_id":p["policy_id"],"tools":p["tools"],"validation_correlation":p["correlation"],"validation_variance_reduction":p["variance_reduction"],"validation_rows":p["rows"],"selected":p["policy_id"]==chosen["policy_id"]})
        oos = pd.DataFrame(oos_rows)
        if not oos.empty:
            primary = metric(oos["target"], oos["proxy_return"], oos["date"], ci=True)
            primary.update({"status":"SEEN_EXPLORATORY" if primary["days"] >= 20 else "SHORT_SAMPLE", "oos_start":str(oos["date"].min()), "oos_end":str(oos["date"].max()), "sample_hash":sample_hash(oos.rename(columns={"target":"target_price"}), ["target_price","proxy_return"]), "selected_policy_counts":oos["policy_id"].value_counts().to_dict()})
        else:
            primary={"status":"UNAVAILABLE","days":0,"rows":0,"correlation":None,"variance_reduction":None,"sample_hash":None,"selected_policy_counts":{}}
        validation_aggregate={}
        if validation_samples:
            vs=pd.DataFrame(validation_samples)
            for pid,g in vs.groupby("policy_id"):
                validation_aggregate[pid]=metric(g["target"],g["proxy"],g["date"],ci=False)
        candidates=[]
        for legs in policies:
            if oos.empty:
                candidates.append({"policy_id":"+".join(legs),"tools":list(legs),"status":"UNAVAILABLE","sample_group_id":"NONE"})
                continue
            q=oos[oos["policy_id"]=="+".join(legs)]
            if len(q) < 3:
                row={"policy_id":"+".join(legs),"tools":list(legs),"status":"UNAVAILABLE","sample_group_id":"ROLLING_VALIDATION_50_10","rows":0,"days":0}
            else:
                row={"policy_id":"+".join(legs),"tools":list(legs),"status":"SEEN_EXPLORATORY","sample_group_id":"SELECTED_DAYS_ONLY","rows":int(len(q)),"days":int(q["date"].nunique()),**metric(q["target"],q["proxy_return"],q["date"],ci=False)}
            if "+".join(legs) in validation_aggregate:
                row["validation_aggregate"]=validation_aggregate["+".join(legs)]
            candidates.append(row)
        all_results[horizon]={"primary":primary,"candidate_metrics":candidates,"validation_metrics":validation_rows,"validation_aggregate":validation_aggregate}
        all_residuals[horizon]=oos
        all_weights[horizon]=daily_weights
    return all_results, all_residuals, all_weights


def choose_primary(result: dict):
    """Return a mapping-level recommendation from an engine result."""
    primary=result.get("primary",{})
    if primary.get("status") == "UNAVAILABLE": return None
    return {"decision":"MATCH" if (primary.get("correlation") or -1)>=.60 and (primary.get("variance_reduction") or -1)>0 else "NO_MATCH_IN_TESTED_SET","correlation":primary.get("correlation"),"variance_reduction":primary.get("variance_reduction"),"status":primary.get("status"),"policy_id":max(primary.get("selected_policy_counts",{}),key=primary.get("selected_policy_counts",{}).get) if primary.get("selected_policy_counts") else None}
