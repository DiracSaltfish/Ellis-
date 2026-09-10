#!/usr/bin/env python3
"""Small, failure-safe health-state writer shared by upload processes.

The file contains no credentials or market payloads.  Uploaders call these
helpers only after their existing success/failure decision, so monitoring can
never change whether a business payload is accepted or retried.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
DEFAULT_STATUS_DIR = Path(__file__).resolve().parent / ".runtime" / "upload_health"


def _status_dir() -> Path:
    return Path(os.getenv("NNN_UPLOAD_HEALTH_DIR", str(DEFAULT_STATUS_DIR))).expanduser()


def _clean_source(source: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(source or "").strip()).strip("-.")
    return value or f"unknown-{os.getpid()}"


def _now() -> datetime:
    return datetime.now(SHANGHAI)


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write(source: str, values: dict[str, Any]) -> None:
    try:
        directory = _status_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{_clean_source(source)}.json"
        current = _read(path)
        current.update(values)
        current.update(
            {
                "schema_version": 1,
                "source": str(source or "").strip(),
                "pid": os.getpid(),
                "updated_at": _now().isoformat(timespec="milliseconds"),
            }
        )
        fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(current, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    except Exception:
        # Monitoring must never break the market-data process.
        return


def record_success(
    source: str,
    *,
    stage: str,
    accepted: int | None = None,
    symbols: Iterable[str] | None = None,
    detail: str = "",
) -> None:
    values: dict[str, Any] = {
        "state": "ok",
        "stage": str(stage or "").strip(),
        "last_success_at": _now().isoformat(timespec="milliseconds"),
        "last_error": "",
    }
    if accepted is not None:
        values["accepted"] = int(accepted)
    if symbols is not None:
        values["symbols"] = sorted({str(item).strip().upper() for item in symbols if str(item).strip()})
    if detail:
        values["detail"] = str(detail)[:500]
    _write(source, values)


def record_failure(source: str, *, stage: str, error: Any) -> None:
    _write(
        source,
        {
            "state": "error",
            "stage": str(stage or "").strip(),
            "last_failure_at": _now().isoformat(timespec="milliseconds"),
            "last_error": str(error or "unknown upload error")[:1000],
        },
    )


def record_heartbeat(source: str, *, stage: str, detail: str = "") -> None:
    values: dict[str, Any] = {
        "state": "ok",
        "stage": str(stage or "heartbeat").strip(),
        "last_heartbeat_at": _now().isoformat(timespec="milliseconds"),
        "last_error": "",
    }
    if detail:
        values["detail"] = str(detail)[:500]
    _write(source, values)
