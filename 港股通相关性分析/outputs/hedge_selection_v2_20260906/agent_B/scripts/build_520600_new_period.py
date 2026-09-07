#!/usr/bin/env python3
"""Build a small, auditable 520600 new-period panel and partial OOS metrics.

The official PCF pages are the basket source.  Eastmoney 5-minute bars are
used only as an explicitly labelled price proxy because the local 1-minute
ETF/HK stock archive stops on 2026-08-03.  The resulting 2026-08-06 onward
sample is exploratory: it is shorter than the 20-day confirmation minimum,
so this script never emits a confirmation pass.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import time
import urllib.parse
import urllib.request
import urllib.error
import gzip
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path("/Users/ellis/工具程序开发/港股通相关性分析")
AGENT = ROOT / "outputs/hedge_selection_v2_20260906/agent_B"
PCF_DIR = AGENT / "data/raw/pcf_html/520600"
QUOTE_DIR = AGENT / "data/raw/eastmoney_new_period"
IB_DIR = AGENT / "data/raw/ibkr_new_period"
OUT_DIR = AGENT / "data/new_period_520600"
ARCHIVE_INPUT = ROOT / "batch_archive_20260906/data/raw/pilot_520600.jsonl.gz"

DATE_START = "20260804"
DATE_END = "20260904"
API_BASE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
API_FIELDS_1 = "f1,f2,f3,f4,f5,f6"
API_FIELDS_2 = "f51,f52,f53,f54,f55,f56"
HORIZONS = [5, 15, 30, 60]
# Last pre-new-period fixed pair from the independently reproduced old run.
PAIR_BETA = {
    5: {"HHI_FUT": 0.4065644528940298, "HTI_FUT": 0.3588191301867448},
    15: {"HHI_FUT": 0.4338586641256397, "HTI_FUT": 0.4452898189379947},
    30: {"HHI_FUT": 0.5092797293653971, "HTI_FUT": 0.4490979543084183},
    60: {"HHI_FUT": 0.7152087030417262, "HTI_FUT": 0.3604268558717037},
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def clean_cell(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", value).strip()


def num(value: str) -> float | None:
    value = clean_cell(value).replace(",", "").replace("%", "")
    if value in {"", "-", "—", "None"}:
        return None
    return float(value)


def parse_pcf(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    date_match = re.search(r'name="date"[^>]+value="(\d{8})"', text)
    date = date_match.group(1) if date_match else path.stem

    def label_value(label: str) -> str:
        m = re.search(rf"<th>{re.escape(label)}.*?</th>\s*<td>(.*?)</td>", text, re.S)
        return clean_cell(m.group(1)) if m else ""

    header = {
        "公告日期": date[:4] + "-" + date[4:6] + "-" + date[6:],
        "基金代码": "520600",
        "基金名称": "汽车港股",
        "预估现金差额元": label_value("最小申购、赎回单位的预估现金部分(单位:元)："),
        "最小申购赎回单位份": label_value("最小申购、赎回单位(单位:份)："),
        "现金替代比例上限百分比": label_value("现金替代比例上限："),
        "是否需要公告IOPV": label_value("是否需要公布IOPV："),
        "申购赎回模式": label_value("申购赎回模式："),
    }
    marker = text.find("<!-- 成分证券列表")
    body = text[marker:] if marker >= 0 else text
    rows = re.findall(r"<tr>\s*((?:<td>.*?</td>\s*){8})</tr>", body, re.S)
    components = []
    for row in rows:
        cells = [clean_cell(x) for x in re.findall(r"<td>(.*?)</td>", row, re.S)]
        if len(cells) != 8 or not re.fullmatch(r"\d{5}", cells[0]):
            continue
        components.append({
            "记录ID": "",
            "公告日期": header["公告日期"],
            "基金代码": "520600",
            "基金名称": "汽车港股",
            "成分股代码": cells[0],
            "成分股名称": cells[1],
            "数量股": str(num(cells[2]) or 0.0),
            "现金替代标志": cells[3],
            "现金替代比例百分比": str(num(cells[4])) if num(cells[4]) is not None else "",
            "固定替代金额元": str(num(cells[6]) or 0.0),
        })
    if not components:
        raise RuntimeError(f"no PCF components parsed: {path}")
    header["成分股数量只"] = str(len(components))
    return {"date": date, "header": header, "components": components, "source_path": str(path), "source_sha256": sha256(path)}


def fetch_quote(secid: str, name: str) -> dict:
    QUOTE_DIR.mkdir(parents=True, exist_ok=True)
    path = QUOTE_DIR / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    query = {
        "secid": secid,
        "fields1": API_FIELDS_1,
        "fields2": API_FIELDS_2,
        "klt": "5",
        "fqt": "0",
        "beg": DATE_START,
        "end": "20260905",
    }
    url = API_BASE + "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last_error = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                payload = json.loads(response.read())
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_error = exc
            time.sleep(1.0 + attempt)
    else:
        # Some local TLS/proxy combinations are intermittently disconnected by
        # this endpoint; curl is the same read-only HTTP request and is used as
        # a bounded fallback, never as a data transformation.
        proc = subprocess.run(
            ["curl", "-L", "--max-time", "30", "-A", "Mozilla/5.0", "-sS", url],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"quote request failed for {secid}: {last_error}; curl={proc.stderr.strip()}")
        payload = json.loads(proc.stdout)
    payload["_source_url"] = url
    payload["_retrieved_at_utc"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    time.sleep(0.12)
    return payload


def quote_map(payload: dict) -> dict[str, dict[int, float]]:
    data = payload.get("data") or {}
    result: dict[str, dict[int, float]] = {}
    for row in data.get("klines") or []:
        fields = row.split(",")
        if len(fields) < 5:
            continue
        stamp, op, close, high, low = fields[:5]
        day, hhmm = stamp.split(" ")
        minute = int(hhmm[:2]) * 60 + int(hhmm[3:5])
        result.setdefault(day.replace("-", ""), {})[minute] = float(close)
    return result


def parse_ib(day: str, name: str) -> dict[int, float]:
    path = IB_DIR / f"{day}_{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for bar in data.get("bars", []):
        ts = datetime.fromtimestamp(int(bar["date"]), tz=timezone.utc).astimezone()
        # The local machine is Asia/Shanghai; HK/Shanghai have the same clock.
        minute = ts.hour * 60 + ts.minute
        out[minute] = float(bar["close"])
    return out


def build_panel(pcfs: list[dict], quote_maps: dict[str, dict], target_map: dict) -> tuple[list[dict], list[dict]]:
    panel = []
    coverage = []
    for pcf in pcfs:
        day = pcf["date"]
        comps = pcf["components"]
        times = list(range(785, 901, 5))  # 13:05..15:00, 5-minute proxy endpoints
        prices = {c["成分股代码"]: quote_maps.get(c["成分股代码"], {}).get(day, {}) for c in comps}
        missing = [c["成分股代码"] for c in comps if not prices[c["成分股代码"]]]
        for t in times:
            vals = []
            missing_at_t = []
            for c in comps:
                code = c["成分股代码"]
                px = prices[code].get(t)
                if px is None:
                    missing_at_t.append(code)
                else:
                    vals.append(float(c["数量股"]) * px)
            basket = float(sum(vals)) if not missing_at_t else float("nan")
            row = {
                "date": day, "minute": t, "basket_hkd": basket,
                "target_etf_close": target_map.get(day, {}).get(t),
                "missing_members": ",".join(missing_at_t),
            }
            for name in ["HSI_U6", "HHI_U6", "HTI_U6"]:
                row[name] = parse_ib(day, name).get(t)
            panel.append(row)
        coverage.append({
            "date": day,
            "pcf_components": len(comps),
            "component_codes_missing_any_bar": ",".join(missing),
            "complete_5m_endpoints": sum(1 for r in panel if r["date"] == day and not r["missing_members"]),
            "expected_5m_endpoints": len(times),
            "target_endpoint_count": sum(1 for t in times if t in target_map.get(day, {})),
            "source_timebase": "5min Eastmoney price proxy + 1min IBKR futures",
        })
    return panel, coverage


def es95(values: np.ndarray) -> tuple[float, float]:
    k = max(1, int(np.ceil(len(values) * 0.05)))
    return float(np.sort(values)[-k:].mean() * 10000), float(np.sort(-values)[-k:].mean() * 10000)


def calculate_metrics(panel: list[dict]) -> list[dict]:
    df = pd.DataFrame(panel)
    df = df.sort_values(["date", "minute"])
    output = []
    for h in HORIZONS:
        rows = []
        for day, g in df.groupby("date", sort=True):
            g = g.set_index("minute").sort_index()
            for t in g.index:
                end = t + h
                if end not in g.index:
                    continue
                a, b = g.loc[t], g.loc[end]
                if not np.isfinite([a.basket_hkd, b.basket_hkd, a.HHI_U6, b.HHI_U6, a.HTI_U6, b.HTI_U6]).all():
                    continue
                y = b.basket_hkd / a.basket_hkd - 1.0
                rhhi = b.HHI_U6 / a.HHI_U6 - 1.0
                rhti = b.HTI_U6 / a.HTI_U6 - 1.0
                beta = PAIR_BETA[h]
                residual = y - beta["HHI_FUT"] * rhhi - beta["HTI_FUT"] * rhti
                rows.append({"date": day, "y": y, "residual": residual})
        r = pd.DataFrame(rows)
        if r.empty:
            output.append({"horizon_min": h, "oos_days": 0, "sample_count": 0})
            continue
        target = r.y.to_numpy(float)
        residual = r.residual.to_numpy(float)
        target_std = float(np.std(target, ddof=1) * 10000)
        residual_std = float(np.std(residual, ddof=1) * 10000)
        up, down = es95(residual)
        target_up, target_down = es95(target)
        block_rows = []
        date_order = list(dict.fromkeys(r.date.tolist()))
        for i in range(0, len(date_order) - 4, 5):
            ds = set(date_order[i:i + 5])
            sub = r[r.date.isin(ds)]
            block_rows.append(float(np.var(sub.y, ddof=1) - np.var(sub.residual, ddof=1)))
        output.append({
            "horizon_min": h,
            "policy_id": "P520600_OLD_FIXED_HHI_HTI",
            "model_id": "HHI_HTI_fixed_pair",
            "scenario_id": "new_period_partial_5m_proxy",
            "confirmation_status": "REUSED_OR_UNPROVEN",
            "oos_start": min(r.date), "oos_end": max(r.date),
            "oos_days": int(r.date.nunique()), "sample_count": int(len(r)),
            "target_std_bp": target_std, "residual_std_bp": residual_std,
            "variance_reduction": float(1 - np.var(residual, ddof=1) / np.var(target, ddof=1)),
            "ci_low": None, "ci_high": None,
            "bootstrap_method": "not_computed: fewer than 20 new OOS days",
            "bootstrap_seed": 520600,
            "target_up_es95_bp": target_up, "target_down_es95_bp": target_down,
            "up_es95_bp": up, "down_es95_bp": down,
            "positive_block_fraction": float(np.mean(np.array(block_rows) > 0)) if block_rows else None,
            "residual_mean_bp": float(np.mean(residual) * 10000),
            "beta_turnover": 0.0,
            "decision_gate_results": {
                "oos_days_ge_20": False,
                "point_vr_ge_50pct": bool(1 - np.var(residual, ddof=1) / np.var(target, ddof=1) >= 0.5),
                "ci_lower_ge_30pct": None,
                "strict_refit": "not_run_due_to_5m_proxy_and_short_sample",
            },
            "notes": "Point estimate only; not a confirmation result. PCF complete for these dates, but component price proxy begins 2026-08-06 and is 5-minute rather than 1-minute.",
        })
    return output


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pcfs = [parse_pcf(p) for p in sorted(PCF_DIR.glob("*.html")) if DATE_START <= p.stem <= DATE_END]
    if not pcfs:
        raise RuntimeError("no official PCF pages found")
    codes = sorted({c["成分股代码"] for p in pcfs for c in p["components"]})
    target_payload = fetch_quote("1.520600", "520600_target_5m")
    target_map = quote_map(target_payload)
    quote_payloads = {}
    for code in codes:
        quote_payloads[code] = fetch_quote(f"116.{code}", code)
    quote_maps = {code: quote_map(payload) for code, payload in quote_payloads.items()}
    panel, coverage = build_panel(pcfs, quote_maps, target_map)
    metrics = calculate_metrics(panel)
    with gzip.open(OUT_DIR / "new_period_520600_panel.jsonl.gz", "wt", encoding="utf-8") as f:
        for row in panel:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    with gzip.open(OUT_DIR / "new_period_520600_pcf.jsonl.gz", "wt", encoding="utf-8") as f:
        for pcf in pcfs:
            f.write(json.dumps(pcf, ensure_ascii=False, separators=(",", ":")) + "\n")
    pd.DataFrame(coverage).to_csv(OUT_DIR / "new_period_520600_coverage.csv", index=False)
    pd.DataFrame(metrics).to_json(OUT_DIR / "new_period_520600_metrics.json", orient="records", force_ascii=False, indent=2)
    pd.DataFrame(metrics).to_csv(OUT_DIR / "new_period_520600_metrics.csv", index=False)
    metadata = {
        "run_id": "B_520600_NEW_PARTIAL_20260906",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "confirmation_window_target": [DATE_START, DATE_END],
        "official_pcf_pages": len(pcfs),
        "unique_pcf_components": len(codes),
        "quote_source": "Eastmoney push2his 5-minute endpoint; price proxy only",
        "target_quote_url": target_payload.get("_source_url"),
        "quote_files": {code: str(QUOTE_DIR / f"{code}.json") for code in ["520600_target_5m", *codes]},
        "panel_path": str(OUT_DIR / "new_period_520600_panel.jsonl.gz"),
        "metrics_path": str(OUT_DIR / "new_period_520600_metrics.csv"),
        "selection_policy": "pre-new-period fixed HHI+HTI weights copied from reproduced 520600 old run; no new-period model selection",
        "confirmation_status": "REUSED_OR_UNPROVEN",
        "limitations": [
            "Only 19 complete component-price days are available from the 5-minute quote proxy; official PCF exists for 21 target dates.",
            "The local 1-minute archive ends on 2026-08-03; new component and target prices use Eastmoney 5-minute bars.",
            "No bootstrap CI or strict refit is claimed for this short proxy sample.",
        ],
    }
    (OUT_DIR / "run_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"pcf_pages": len(pcfs), "components": len(codes), "metrics": metrics}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
