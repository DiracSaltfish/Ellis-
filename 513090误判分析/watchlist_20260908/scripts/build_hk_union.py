"""Build the deduplicated HK constituent master from both exchange PCF sets."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
master: dict[str, dict[str, object]] = {}


def add(code: str, name: str, fund: str, source: str) -> None:
    code = code.strip().zfill(5)
    rec = master.setdefault(code, {"name": name, "funds": [], "sources": []})
    if name and not rec["name"]:
        rec["name"] = name
    if fund not in rec["funds"]:
        rec["funds"].append(fund)
    if source not in rec["sources"]:
        rec["sources"].append(source)


for path in sorted((ROOT / "raw" / "pcf").glob("*_20260908.xml")):
    fund = path.name.split("_")[0]
    root = ET.parse(path).getroot()
    for node in root.iter():
        if node.tag.split("}")[-1] != "Component":
            continue
        row = {x.tag.split("}")[-1]: (x.text or "").strip() for x in node}
        if row.get("UnderlyingSecurityIDSource") == "103" and row.get("UnderlyingSecurityID", "").isdigit():
            add(row["UnderlyingSecurityID"], row.get("UnderlyingSymbol", ""), fund, "SZSE")

for path in sorted((ROOT / "raw" / "pcf_sse").glob("*_20260908.xml")):
    fund = path.name.split("_")[0]
    root = ET.parse(path).getroot()
    for node in root.iter():
        if node.tag.split("}")[-1] != "Component":
            continue
        row = {x.tag.split("}")[-1]: (x.text or "").strip() for x in node}
        if row.get("UnderlyingSecurityID") == "103" and row.get("InstrumentID", "").isdigit():
            add(row["InstrumentID"], row.get("InstrumentName", ""), fund, "SSE")

out = {k: master[k] for k in sorted(master)}
(ROOT / "inputs" / "hk_union_all_pcf.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
print(json.dumps({"unique_hk": len(out), "from_sz": sum("SZSE" in v["sources"] for v in out.values()), "from_sse": sum("SSE" in v["sources"] for v in out.values())}, ensure_ascii=False))
