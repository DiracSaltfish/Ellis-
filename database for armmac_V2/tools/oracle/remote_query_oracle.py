#!/usr/bin/env python3
"""Call the bj relay query endpoint without exposing configured secrets/data.

This helper is meant to run on the authorized Linux oracle host.  It reads the
relay's token only in memory and prints JSON structure metadata, never scalar
values from the upstream response.
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def safe_shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): safe_shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return {
            "type": "list",
            "length": len(value),
            "item": safe_shape(value[0]) if value else None,
        }
    return {"type": type(value).__name__}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("/opt/galaxy-relay/config/config.yaml"))
    parser.add_argument("--env-file", type=Path, default=Path("/etc/galaxy-relay/relay.env"))
    parser.add_argument("--url", default="http://127.0.0.1:18700/v1/query/etf-pcf")
    parser.add_argument("--code", default="510300.SH")
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    env = load_env_file(args.env_file)
    token_name = config["security"]["api_token_env"]
    token = env.get(token_name, "")
    if not token:
        raise RuntimeError("configured relay API token is unavailable")

    request = urllib.request.Request(
        args.url,
        data=json.dumps({"codes": [args.code]}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            value = json.load(response)
            print(json.dumps({"http": response.status, "shape": safe_shape(value)}, sort_keys=True))
    except urllib.error.HTTPError as exc:
        # The response body may echo sensitive diagnostics; report status only.
        print(json.dumps({"http": exc.code, "error": "HTTPError"}, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
