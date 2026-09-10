#!/usr/bin/env python3
"""Stage raw one-minute history for the currently PCF-backed IOPV subscriptions.

The native Linux SDK runs only on bj. Its credentials stay in the server's
EnvironmentFile; this program transports only returned market-data records.
Each completed compressed chunk is validated locally, so a later invocation
resumes without re-querying it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import shlex
import subprocess
import time
from pathlib import Path

REMOTE_QUERY = r'''
import AmazingData as ad, gzip, json, os, sys, time, tgw
request, out = json.loads(sys.argv[1]), sys.argv[2]
result = {"schema": "tgw-native-history-chunk.v1", "begin": request["begin"], "end": request["end"], "symbols": request["symbols"], "errors": {}}
market = {"SH": tgw.MarketType.kSSE, "SZ": tgw.MarketType.kSZSE, "HK": tgw.MarketType.kHKEx}
with gzip.open(out, "wt", encoding="utf-8", compresslevel=6) as handle:
    handle.write(json.dumps({"type": "header", **result}, ensure_ascii=False, separators=(",", ":")) + "\n")
    try:
        ad.login(username=os.environ["AMAZINGDATA_USERNAME"], password=os.environ["AMAZINGDATA_PASSWORD"], host="101.230.159.234", port=8600)
        for symbol in request["symbols"]:
            try:
                code, suffix = symbol.split(".")
                q = tgw.ReqKline(); q.security_code = code; q.market_type = market[suffix]
                q.cq_flag = q.cq_date = q.qj_flag = q.cyc_def = 0; q.cyc_type = 10000; q.auto_complete = 1
                q.begin_date = request["begin"]; q.end_date = request["end"]; q.begin_time = 930; q.end_time = 1600
                rows, err = tgw.QueryKline(q, return_df_format=False)
                if err or rows is None:
                    result["errors"][symbol] = "query_error"
                    handle.write(json.dumps({"type": "symbol", "symbol": symbol, "rows": []}, separators=(",", ":")) + "\n")
                else:
                    handle.write(json.dumps({"type": "symbol", "symbol": symbol, "rows": rows}, ensure_ascii=False, separators=(",", ":")) + "\n")
            except Exception as exc:
                result["errors"][symbol] = type(exc).__name__
                handle.write(json.dumps({"type": "symbol", "symbol": symbol, "rows": []}, separators=(",", ":")) + "\n")
    finally:
        try: ad.logout(os.environ.get("AMAZINGDATA_USERNAME", ""))
        except Exception: pass
    result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    handle.write(json.dumps({"type": "footer", **result}, ensure_ascii=False, separators=(",", ":")) + "\n")
'''


def run(args: list[str], *, input_text: str | None = None) -> str:
    return subprocess.run(args, input=input_text, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout


def atomic_json(path: Path, value: object) -> None:
    tmp = path.with_suffix(path.suffix + ".next")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def inspect_chunk(path: Path, expected_symbols: list[str]) -> dict:
    seen, footer, rows = set(), None, 0
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            if item.get("type") == "symbol":
                seen.add(item["symbol"])
                rows += len(item.get("rows", []))
            elif item.get("type") == "footer":
                footer = item
    if footer is None or seen != set(expected_symbols):
        raise ValueError(f"incomplete chunk {path.name}")
    return {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size, "symbols": len(seen), "rows": rows, "errors": footer.get("errors", {})}


def install_remote_worker() -> None:
    worker = Path(__file__).with_name("tgw_history_chunk_worker.py")
    if not worker.is_file():
        raise FileNotFoundError(worker)
    run(["scp", str(worker), "bj:/tmp/tgw-history-chunk-worker.py"])


def fetch_chunk(index: int, symbols: list[str], begin: int, end: int, output: Path) -> dict:
    target = output / "chunks" / f"chunk-{index:04d}.jsonl.gz"
    if target.exists():
        return inspect_chunk(target, symbols)
    remote = f"/tmp/iopv-history-{begin}-{end}-{index:04d}.jsonl.gz"
    request = json.dumps({"symbols": symbols, "begin": begin, "end": end}, separators=(",", ":"))
    log = remote + ".log"
    shell = "set -euo pipefail; test ! -e " + shlex.quote(remote) + "; set -a; . /etc/galaxy-relay/relay.env; set +a; nohup /opt/galaxy-relay/venv/bin/python /tmp/tgw-history-chunk-worker.py " + shlex.quote(request) + " " + shlex.quote(remote) + " >" + shlex.quote(log) + " 2>&1 < /dev/null &"
    run(["ssh", "bj", "sudo -n /bin/bash -c " + shlex.quote(shell)])
    for _ in range(180):
        probe = subprocess.run(["ssh", "bj", "sudo", "-n", "gzip", "-t", remote], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if probe.returncode == 0:
            break
        time.sleep(10)
    else:
        raise TimeoutError(f"remote chunk did not complete: {index}")
    try:
        run(["scp", f"bj:{remote}", str(target)])
        detail = inspect_chunk(target, symbols)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    run(["ssh", "bj", "sudo", "-n", "rm", "-f", remote, log])
    return detail


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--begin", required=True, type=int)
    parser.add_argument("--end", required=True, type=int)
    parser.add_argument("--chunk-size", type=int, default=20)
    args = parser.parse_args()
    if len(str(args.begin)) != 8 or len(str(args.end)) != 8 or args.begin > args.end or args.chunk_size < 1:
        raise ValueError("invalid range or chunk size")
    manifest = json.loads(Path(args.manifest).read_text())
    symbols = manifest.get("subscriptions")
    if manifest.get("schema") != "iopv-subscription-history-stage.v1" or not isinstance(symbols, list) or not symbols:
        raise ValueError("invalid subscription manifest")
    if len(symbols) != len(set(symbols)) or any(not isinstance(s, str) or not s.endswith((".SH", ".SZ", ".HK")) for s in symbols):
        raise ValueError("invalid subscription symbols")
    install_remote_worker()
    output = Path(args.output).resolve()
    if not output.is_dir():
        raise ValueError("output must be a pre-created staging directory")
    (output / "chunks").mkdir(mode=0o700, exist_ok=True)
    metadata = {"schema": "iopv-subscription-history-stage.v1", "created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "manifest_sha256": hashlib.sha256(Path(args.manifest).read_bytes()).hexdigest(), "begin": args.begin, "end": args.end, "chunk_size": args.chunk_size, "symbols": len(symbols), "source": "bj native TGW SDK minute K-line"}
    metadata_path = output / "capture-metadata.json"
    if metadata_path.exists():
        existing = json.loads(metadata_path.read_text())
        if any(existing.get(key) != metadata[key] for key in ("schema", "manifest_sha256", "begin", "end", "chunk_size", "symbols")):
            raise ValueError("existing staging metadata does not match request")
    else:
        atomic_json(metadata_path, metadata)
    state_path = output / "capture-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"schema": metadata["schema"], "begin": args.begin, "end": args.end, "chunks": [], "complete": False}
    completed = {item["file"]: item for item in state["chunks"]}
    for index, start in enumerate(range(0, len(symbols), args.chunk_size)):
        detail = fetch_chunk(index, symbols[start:start + args.chunk_size], args.begin, args.end, output)
        completed[detail["file"]] = detail
        state["chunks"] = [completed[name] for name in sorted(completed)]
        state["completed_symbols"] = sum(item["symbols"] for item in state["chunks"])
        state["received_rows"] = sum(item["rows"] for item in state["chunks"])
        state["errors"] = {symbol: reason for item in state["chunks"] for symbol, reason in item["errors"].items()}
        atomic_json(output / "capture-state.json", state)
        print(json.dumps({"chunk": index, "completed_symbols": state["completed_symbols"], "received_rows": state["received_rows"], "errors": len(state["errors"])}, ensure_ascii=False), flush=True)
    state["complete"] = True
    atomic_json(output / "capture-state.json", state)


if __name__ == "__main__":
    main()
