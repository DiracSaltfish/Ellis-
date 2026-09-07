#!/usr/bin/env python3
"""Build 520600's PCF fixed-quantity basket from real 1-minute inputs."""
from __future__ import annotations

import csv
import gzip
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
PCF = ROOT / "data/raw/new_period_520600/520600_pcf_parsed.jsonl.gz"
STK = ROOT / "data/raw/new_period_520600/520600_stk_1m.jsonl.gz"
IB = ROOT / "data/raw/ibkr_new_period"
OUT = ROOT / "data/raw/new_period_520600"
HK = ZoneInfo("Asia/Hong_Kong")


def read_pcfs():
    with gzip.open(PCF, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def load_stock_prices():
    prices = defaultdict(lambda: defaultdict(dict))
    records = 0
    with gzip.open(STK, "rt", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line); records += 1
            code = rec.get("security_id")
            for bar in rec.get("bars", []):
                stamp = datetime.fromtimestamp(int(bar["date"]), tz=timezone.utc).astimezone(HK)
                prices[code][stamp.strftime("%Y%m%d")][stamp.hour * 60 + stamp.minute] = float(bar["close"])
    return prices, records


def load_futures():
    prices = defaultdict(dict); files = 0
    for name in ("HSI_U6", "HHI_U6", "HTI_U6"):
        for path in sorted(IB.glob(f"*_{name}.json")):
            day = path.name.split("_", 1)[0]; data = json.loads(path.read_text(encoding="utf-8")); files += 1
            prices[name][day] = {}
            for bar in data.get("bars", []):
                stamp_raw = bar["date"]
                if str(stamp_raw).isdigit():
                    stamp = datetime.fromtimestamp(int(stamp_raw), tz=timezone.utc).astimezone(HK)
                    minute = stamp.hour * 60 + stamp.minute
                else:
                    stamp = datetime.fromisoformat(str(stamp_raw).replace(" ", "T")); minute = stamp.hour * 60 + stamp.minute
                prices[name][day][minute] = float(bar["close"])
    return prices, files


def main():
    pcfs = read_pcfs(); stock, stock_records = load_stock_prices(); futures, future_files = load_futures()
    panel, coverage = [], []
    for pcf in pcfs:
        day = pcf["date"]; comps = pcf["components"]; nonzero = [c for c in comps if (c.get("数量股") or 0) > 0]
        maps = {c["成分股代码"]: stock.get(c["成分股代码"], {}).get(day, {}) for c in nonzero}
        missing_codes = [c["成分股代码"] for c in nonzero if not maps[c["成分股代码"]]]
        common = set(futures["HSI_U6"].get(day, {})) & set(futures["HHI_U6"].get(day, {})) & set(futures["HTI_U6"].get(day, {}))
        for code, values in maps.items(): common &= set(values)
        common = sorted(m for m in common if 570 <= m <= 960 and (m < 720 or m >= 780))
        for minute in common:
            values = [float(c.get("数量股") or 0) * maps[c["成分股代码"]][minute] for c in nonzero]
            basket = float(sum(values))
            panel.append({"fund_id": "520600.SH", "date": day, "minute": minute, "basket_hkd": basket, "HSI_U6": futures["HSI_U6"][day][minute], "HHI_U6": futures["HHI_U6"][day][minute], "HTI_U6": futures["HTI_U6"][day][minute], "pcf_component_count": len(comps), "priced_component_count": len(nonzero), "missing_component_codes": ",".join(missing_codes), "price_source": "IBKR STK 1m; PCF fixed quantities"})
        coverage.append({"date": day, "pcf_component_count": len(comps), "nonzero_component_count": len(nonzero), "components_with_any_price": sum(bool(maps[c["成分股代码"]]) for c in nonzero), "missing_component_codes": missing_codes, "common_minute_count": len(common), "expected_common_minute_count": 330, "futures_present": all(bool(futures[name].get(day)) for name in ("HSI_U6", "HHI_U6", "HTI_U6")), "status": "PASS" if len(common) >= 300 and not missing_codes else "PARTIAL"})
    OUT.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT / "520600_basket_panel_1m.jsonl.gz", "wt", encoding="utf-8") as f:
        for r in panel: f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    with (OUT / "520600_basket_coverage.json").open("w", encoding="utf-8") as f: json.dump(coverage, f, ensure_ascii=False, indent=2)
    with (OUT / "520600_basket_coverage.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fields = list(coverage[0].keys()) if coverage else ["date"]; w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(coverage)
    meta = {"run_id": "B-520600-BASKET-1M-R1", "generated_at_utc": datetime.now(timezone.utc).isoformat(), "pcf_pages": len(pcfs), "stock_records": stock_records, "futures_files": future_files, "panel_rows": len(panel), "panel_path": str((OUT / "520600_basket_panel_1m.jsonl.gz").relative_to(ROOT.parent.parent)), "coverage_path": str((OUT / "520600_basket_coverage.json").relative_to(ROOT.parent.parent)), "assumptions": ["PCF quantities are fixed per published date", "cash-substitution rows with zero share quantity contribute no price risk", "HKD basket and HKD tools use same-currency returns; settlement FX is retained as ex-post context, not intraday signal", "1-minute bar label is treated as bar-start/session clock"]}
    (OUT / "520600_basket_metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"panel_rows": len(panel), "coverage": coverage, "metadata": meta}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
