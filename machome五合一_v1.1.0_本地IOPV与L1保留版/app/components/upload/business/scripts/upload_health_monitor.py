#!/usr/bin/env python3
"""Mac-home upload supervisor with independent PushPlus notifications."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_STATUS_DIR = SCRIPT_DIR / ".runtime" / "upload_health"
DEFAULT_STATE_FILE = SCRIPT_DIR / ".runtime" / "upload_health_monitor_state.json"
DEFAULT_RUNTIME_DIR = SCRIPT_DIR / ".runtime"
PUBLIC_LABEL = "com.newnavnav.sina-quote-uploader"
NIKKEI_LABEL = "com.newnavnav.private-nikkei225-valuation-uploader"
NIKKEI_SOURCE = "mac-home-private-nikkei225-pcf-n225m-uploader"
PUBLIC_MONITOR_END = 14 * 3600 + 57 * 60
NIKKEI_MONITOR_END = 14 * 3600 + 40 * 60
PRIVATE_MONITOR_END = 15 * 3600
DEFAULT_LABELS = (
    PUBLIC_LABEL,
    "com.newnavnav.private-xop-family-uploader",
    "com.newnavnav.private-china-internet-valuation-uploader",
    "com.newnavnav.private-nasdaq-valuation-uploader",
    "com.newnavnav.private-sp500-valuation-uploader",
    NIKKEI_LABEL,
    "com.newnavnav.private-germany-valuation-uploader",
    "com.newnavnav.private-164824-valuation-uploader",
    "com.newnavnav.private-161226-silver-uploader",
)
DEFAULT_PRIVATE_SOURCES = (
    "mac-home-private-xop-family-uploader",
    "mac-home-private-china-internet-uploader",
    "mac-home-private-nasdaq-pcf-nq-uploader",
    "mac-home-private-sp500-pcf-es-uploader",
    NIKKEI_SOURCE,
    "mac-home-private-germany-pcf-fdxm-xetra1735-uploader",
    "mac-home-private-164824-t2-inda-uploader",
)
DEFAULT_PRIVATE_SOURCES += (
    "mac-home-private-161226-silver-uploader",
)
DEFAULT_PUBLIC_SOURCES = ("home-mac",)
DEFAULT_IGNORED_SYMBOLS = (
    "SZ159605", "SZ159607", "SH513050", "SH513220",
    "SZ159501", "SZ159513", "SZ159632", "SZ159659", "SZ159660", "SZ159696", "SZ159941",
    "SH513100", "SH513110", "SH513300", "SH513390", "SH513870",
    "SZ159612", "SZ159655", "SH513500", "SH513650",
)
DEFAULT_IGNORED_LABELS = (
    "com.newnavnav.private-china-internet-valuation-uploader",
    "com.newnavnav.private-nasdaq-valuation-uploader",
    "com.newnavnav.private-sp500-valuation-uploader",
)
DEFAULT_IGNORED_SOURCES = (
    "mac-home-private-china-internet-uploader",
    "mac-home-private-nasdaq-pcf-nq-uploader",
    "mac-home-private-sp500-pcf-es-uploader",
)
SILVER_SOURCE = "mac-home-private-161226-silver-uploader"
GERMANY_SOURCE = "mac-home-private-germany-pcf-fdxm-xetra1735-uploader"
XOP_SOURCE = "mac-home-private-xop-family-uploader"
INDIA_SOURCE = "mac-home-private-164824-t2-inda-uploader"
EXPECTED_SOURCE_SYMBOLS = {
    XOP_SOURCE: frozenset(("SZ159518", "SH513350", "SZ162411")),
    NIKKEI_SOURCE: frozenset(("SH513000", "SH513520", "SH513880", "SZ159866")),
    GERMANY_SOURCE: frozenset(("SH513030", "SZ159561")),
    INDIA_SOURCE: frozenset(("SZ164824",)),
    SILVER_SOURCE: frozenset(("SZ161226",)),
}
LABEL_SOURCES = {
    PUBLIC_LABEL: "home-mac",
    "com.newnavnav.private-xop-family-uploader": XOP_SOURCE,
    NIKKEI_LABEL: NIKKEI_SOURCE,
    "com.newnavnav.private-germany-valuation-uploader": GERMANY_SOURCE,
    "com.newnavnav.private-164824-valuation-uploader": INDIA_SOURCE,
    "com.newnavnav.private-161226-silver-uploader": SILVER_SOURCE,
}
DEFAULT_PCF_PATHS = (
    ("SZ159518", "private_xop_family/159518/pcf/{date_dash}/159518.xml"),
    ("SH513350", "private_xop_family/513350/pcf/{date_dash}/513350.json"),
    ("SZ159605", "private_china_internet/pcf/159605/{date_compact}.xml"),
    ("SZ159607", "private_china_internet/pcf/159607/{date_compact}.xml"),
    ("SH513050", "private_china_internet/pcf/513050/{date_compact}.xml"),
    ("SH513220", "private_china_internet/pcf/513220/{date_compact}.xml"),
    ("SZ159501", "private_nasdaq/pcf/159501/{date_compact}.xml"),
    ("SZ159513", "private_nasdaq/pcf/159513/{date_compact}.xml"),
    ("SZ159632", "private_nasdaq/pcf/159632/{date_compact}.xml"),
    ("SZ159659", "private_nasdaq/pcf/159659/{date_compact}.xml"),
    ("SZ159660", "private_nasdaq/pcf/159660/{date_compact}.xml"),
    ("SZ159696", "private_nasdaq/pcf/159696/{date_compact}.xml"),
    ("SZ159941", "private_nasdaq/pcf/159941/{date_compact}.xml"),
    ("SH513100", "private_nasdaq/pcf/513100/{date_compact}.xml"),
    ("SH513110", "private_nasdaq/pcf/513110/{date_compact}.xml"),
    ("SH513300", "private_nasdaq/pcf/513300/{date_compact}.xml"),
    ("SH513390", "private_nasdaq/pcf/513390/{date_compact}.xml"),
    ("SH513870", "private_nasdaq/pcf/513870/{date_compact}.xml"),
    ("SZ159612", "private_sp500/pcf/159612/{date_compact}.xml"),
    ("SZ159655", "private_sp500/pcf/159655/{date_compact}.xml"),
    ("SH513500", "private_sp500/pcf/513500/{date_compact}.xml"),
    ("SH513650", "private_sp500/pcf/513650/{date_compact}.xml"),
    ("SZ159866", "private_nikkei225/pcf/159866/{date_compact}.xml"),
    ("SH513000", "private_nikkei225/pcf/513000/{date_compact}.xml"),
    ("SH513520", "private_nikkei225/pcf/513520/{date_compact}.xml"),
    ("SH513880", "private_nikkei225/pcf/513880/{date_compact}.xml"),
    ("SZ159561", "private_germany/pcf/159561/{date_compact}.xml"),
    ("SH513030", "private_germany/pcf/513030/{date_compact}.xml"),
)


@dataclass(frozen=True)
class Problem:
    key: str
    title: str
    detail: str
    severity: str = "critical"


def incident_confirmation_seconds(key: str) -> float:
    # PCF deadlines and scheduled reports keep their existing semantics.
    return 60.0 if key.startswith(("upload.process.", "upload.tws.", "upload.public.", "upload.private.", "upload.source.")) else 0.0


def source_incident_key(key: str) -> str:
    # Missing/error/stale/coverage are mutually exclusive symptoms of the same
    # source outage. A reason change is not a recovery and must not reset timers.
    if key.startswith(("upload.public.", "upload.private.", "upload.source.")):
        source, _, reason = key.rpartition(".")
        if source.count(".") >= 2 and reason in {"missing", "error", "stale", "coverage"}:
            return source + ".health"
    return key


def split_csv(value: str, fallback: Iterable[str] = ()) -> tuple[str, ...]:
    items = tuple(item.strip() for item in str(value or "").replace("，", ",").split(",") if item.strip())
    return items or tuple(fallback)


def parse_duration(value: str, fallback: float) -> float:
    text = str(value or "").strip().lower()
    if not text:
        return fallback
    units = (("ms", 0.001), ("s", 1.0), ("m", 60.0), ("h", 3600.0))
    for suffix, multiplier in units:
        if text.endswith(suffix):
            try:
                return float(text[: -len(suffix)]) * multiplier
            except ValueError:
                return fallback
    try:
        return float(text)
    except ValueError:
        return fallback


def parse_bool(value: str | None, fallback: bool) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return fallback
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return fallback


def parse_clock(value: str, fallback: int) -> int:
    for layout in ("%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(str(value or "").strip(), layout)
            return parsed.hour * 3600 + parsed.minute * 60 + parsed.second
        except ValueError:
            continue
    return fallback


def seconds_of_day(value: datetime) -> int:
    return value.hour * 3600 + value.minute * 60 + value.second


def is_silver_collection_window(value: datetime) -> bool:
    if value.weekday() >= 5:
        return False
    second = seconds_of_day(value)
    return (
        9 * 3600 + 15 * 60 <= second < 10 * 3600 + 15 * 60
        or 10 * 3600 + 31 * 60 <= second < 11 * 3600 + 30 * 60
        or 13 * 3600 + 31 * 60 <= second < PRIVATE_MONITOR_END
    )


def is_domestic_lunch_monitor_pause(value: datetime) -> bool:
    second = seconds_of_day(value)
    return 11 * 3600 + 30 * 60 <= second < 13 * 3600 + 60


def parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=SHANGHAI) if parsed.tzinfo is None else parsed.astimezone(SHANGHAI)


def positive_age(now: datetime, observed: datetime | None) -> float:
    if observed is None:
        return float("inf")
    return max(0.0, (now - observed).total_seconds())


def compact_seconds(value: float) -> str:
    if value == float("inf"):
        return "never"
    if value >= 3600:
        return f"{value / 3600:.1f}h"
    if value >= 60:
        return f"{value / 60:.1f}m"
    return f"{value:.0f}s"


class PushPlus:
    def __init__(self) -> None:
        self.token = os.getenv("PUSHPLUS_TOKEN", "").strip()
        self.endpoint = os.getenv("PUSHPLUS_ENDPOINT", "https://www.pushplus.plus/send").strip()
        self.channel = os.getenv("PUSHPLUS_CHANNEL", "wechat").strip() or "wechat"
        self.timeout = parse_duration(os.getenv("PUSHPLUS_TIMEOUT", "10s"), 10.0)

    def send(self, title: str, content: str) -> None:
        if not self.token:
            raise RuntimeError("PUSHPLUS_TOKEN is not configured")
        payload = json.dumps(
            {
                "token": self.token,
                "title": title.strip(),
                "content": content.strip(),
                "template": "markdown",
                "channel": self.channel,
                "timestamp": int((time.time() + 120) * 1000),
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "newnavnav-upload-monitor/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read(65536).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"PushPlus HTTP {exc.code}") from exc
        if not isinstance(result, dict) or int(result.get("code") or 0) != 200:
            raise RuntimeError(f"PushPlus rejected request: code={result.get('code') if isinstance(result, dict) else 'invalid'}")


class State:
    def __init__(self, path: Path, sender: PushPlus, repeat_seconds: float) -> None:
        self.path = path
        self.sender = sender
        self.repeat_seconds = max(360.0, repeat_seconds)
        self.retry_seconds = 30.0
        self.data: dict[str, Any] = {"incidents": {}, "sent": {}}
        self._load()

    def _load(self) -> None:
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                self.data = parsed
        except (OSError, ValueError, TypeError):
            pass
        self.data.setdefault("incidents", {})
        self.data.setdefault("sent", {})
        restored: dict[str, Any] = {}
        for key, current in self.data["incidents"].items():
            notified = parse_timestamp(current.get("last_notified_at"))
            if incident_confirmation_seconds(key) and notified is None:
                print(f"monitor pending incident cancelled key={key} reason=monitor_restarted", flush=True)
                continue
            canonical = source_incident_key(key)
            existing = restored.get(canonical)
            previous_notified = parse_timestamp(existing.get("last_notified_at")) if existing else None
            # Preserve the most recent delivered notification when migrating old
            # per-reason incidents, so upgrades cannot bypass the six-minute limit.
            if existing is None or (notified is not None and (previous_notified is None or notified > previous_notified)):
                restored[canonical] = current
        self.data["incidents"] = restored

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def reconcile(self, scope: str, problems: list[Problem], now: datetime, paused_scopes: Iterable[str] = ()) -> None:
        incidents = self.data["incidents"]
        paused_scopes = tuple(paused_scopes)
        for paused in paused_scopes:
            self.pause(paused, now)
        # A new Chinese business date starts a fresh realtime confirmation.
        # Old incidents must neither notify immediately nor report a recovery.
        for key, current in list(incidents.items()):
            observed = parse_timestamp(current.get("last_observed_at")) or parse_timestamp(current.get("opened_at"))
            if key.startswith(scope) and incident_confirmation_seconds(key) and observed and observed.astimezone(SHANGHAI).date() != now.astimezone(SHANGHAI).date():
                del incidents[key]
        problems = [Problem(source_incident_key(item.key), item.title, item.detail, item.severity) for item in problems]
        problems = [item for item in problems if not item.key.startswith(paused_scopes)]
        active = {problem.key: problem for problem in problems}
        for problem in problems:
            if problem.key not in incidents and incident_confirmation_seconds(problem.key):
                print(f"{now.isoformat()} monitor incident pending key={problem.key} confirm_after=60s detail={problem.detail}", flush=True)
            current = incidents.get(problem.key) or {
                "opened_at": now.isoformat(),
                "last_notified_at": "",
                "last_attempt_at": "",
                "good_count": 0,
            }
            current.update(
                {
                    "title": problem.title,
                    "detail": problem.detail,
                    "severity": problem.severity,
                    "last_observed_at": now.isoformat(timespec="seconds"),
                    "good_count": 0,
                }
            )
            last_notified = parse_timestamp(current.get("last_notified_at"))
            last_attempt = parse_timestamp(current.get("last_attempt_at"))
            should_send = last_notified is None or positive_age(now, last_notified) >= self.repeat_seconds
            can_attempt = last_attempt is None or positive_age(now, last_attempt) >= self.retry_seconds
            opened = parse_timestamp(current.get("opened_at")) or now
            confirmed = last_notified is not None or positive_age(now, opened) >= incident_confirmation_seconds(problem.key)
            if confirmed and should_send and can_attempt:
                current["last_attempt_at"] = now.isoformat(timespec="seconds")
                incidents[problem.key] = current
                self.save()
                try:
                    self.sender.send(
                        "【NAVNAV Upload 异常】" + problem.title,
                        "\n".join(
                            (
                                "## Upload 监控告警",
                                "",
                                f"- 级别：{problem.severity}",
                                f"- 首次发现：{current['opened_at']}",
                                f"- 本次检查：{now.isoformat(timespec='seconds')}",
                                f"- 详情：{problem.detail}",
                                *(('- 确认：异常已连续存在至少 1 分钟',) if incident_confirmation_seconds(problem.key) else ()),
                                "",
                                "同一事件持续期间，重复通知间隔不少于 6 分钟。",
                            )
                        ),
                    )
                except Exception as exc:
                    print(f"{now.isoformat()} pushplus incident failed key={problem.key}: {exc}", file=sys.stderr, flush=True)
                else:
                    current["last_notified_at"] = now.isoformat(timespec="seconds")
                    print(f"{now.isoformat()} monitor incident notified key={problem.key} opened_at={current['opened_at']}", flush=True)
            incidents[problem.key] = current

        for key in list(incidents):
            if not key.startswith(scope) or key in active or key.startswith(paused_scopes):
                continue
            current = incidents[key]
            if parse_timestamp(current.get("last_notified_at")) is None:
                del incidents[key]
                print(f"{now.isoformat()} monitor pending incident cancelled key={key} reason=healthy", flush=True)
                continue
            current["good_count"] = int(current.get("good_count") or 0) + 1
            if current["good_count"] < 3:
                incidents[key] = current
                continue
            del incidents[key]
            try:
                self.sender.send(
                    "【NAVNAV Upload 恢复】" + str(current.get("title") or key),
                    "\n".join(
                        (
                            "## Upload 故障已恢复",
                            "",
                            f"- 事件：{current.get('title') or key}",
                            f"- 开始：{current.get('opened_at')}",
                            f"- 恢复：{now.isoformat(timespec='seconds')}",
                        )
                    ),
                )
            except Exception as exc:
                print(f"{now.isoformat()} pushplus recovery failed key={key}: {exc}", file=sys.stderr, flush=True)
            else:
                print(f"{now.isoformat()} monitor recovery notified key={key}", flush=True)
        self.save()

    def pause(self, scope: str, now: datetime) -> None:
        incidents = self.data["incidents"]
        changed = False
        for key, current in incidents.items():
            if key.startswith(scope) and current.get("good_count"):
                current["good_count"] = 0
                changed = True
        pending = [
            key for key, current in incidents.items()
            if key.startswith(scope) and incident_confirmation_seconds(key)
            and parse_timestamp(current.get("last_notified_at")) is None
        ]
        for key in pending:
            del incidents[key]
            print(f"{now.isoformat()} monitor pending incident cancelled key={key} reason=outside_monitor_window", flush=True)
        if pending or changed:
            self.save()

    def send_once(self, key: str, title: str, content: str, now: datetime) -> bool:
        if key in self.data["sent"]:
            return True
        try:
            self.sender.send(title, content)
        except Exception as exc:
            print(f"{now.isoformat()} pushplus scheduled failed key={key}: {exc}", file=sys.stderr, flush=True)
            return False
        self.data["sent"][key] = now.isoformat(timespec="seconds")
        cutoff = now - timedelta(days=14)
        self.data["sent"] = {
            item_key: sent_at
            for item_key, sent_at in self.data["sent"].items()
            if (parse_timestamp(sent_at) or now) >= cutoff
        }
        self.save()
        return True


class Monitor:
    def __init__(self) -> None:
        self.sender = PushPlus()
        self.enabled = parse_bool(os.getenv("PUSHPLUS_MONITOR_ENABLED"), self.sender.token != "")
        self.interval = max(1.0, parse_duration(os.getenv("NNN_UPLOAD_MONITOR_INTERVAL", "5s"), 5.0))
        self.startup_grace = max(0.0, parse_duration(os.getenv("NNN_UPLOAD_MONITOR_STARTUP_GRACE", "120s"), 120.0))
        self.freshness = max(15.0, parse_duration(os.getenv("NNN_UPLOAD_MONITOR_FRESHNESS", "35s"), 35.0))
        self.ignored_symbols = {
            symbol.upper() for symbol in split_csv(os.getenv("NNN_UPLOAD_MONITOR_IGNORED_SYMBOLS", ""), DEFAULT_IGNORED_SYMBOLS)
        }
        ignored_labels = set(split_csv(os.getenv("NNN_UPLOAD_MONITOR_IGNORED_LABELS", ""), DEFAULT_IGNORED_LABELS))
        ignored_sources = set(split_csv(os.getenv("NNN_UPLOAD_MONITOR_IGNORED_SOURCES", ""), DEFAULT_IGNORED_SOURCES))
        configured_labels = split_csv(os.getenv("NNN_UPLOAD_MONITOR_EXPECTED_LABELS", ""), DEFAULT_LABELS)
        self.labels = tuple(label for label in configured_labels if label not in ignored_labels)
        self.public_sources = split_csv(os.getenv("NNN_UPLOAD_MONITOR_PUBLIC_SOURCES", ""), DEFAULT_PUBLIC_SOURCES)
        legacy_private_sources = os.getenv("NNN_UPLOAD_MONITOR_REALTIME_SOURCES", "")
        configured_private = split_csv(os.getenv("NNN_UPLOAD_MONITOR_PRIVATE_SOURCES", legacy_private_sources), DEFAULT_PRIVATE_SOURCES)
        self.private_sources = tuple(
            source for source in configured_private if source not in self.public_sources and source not in ignored_sources
        )
        self.tws_endpoints = split_csv(os.getenv("NNN_UPLOAD_MONITOR_TWS_ENDPOINTS", "192.168.1.111:7496"))
        self.runtime_dir = Path(os.getenv("NNN_UPLOAD_RUNTIME_DIR", str(DEFAULT_RUNTIME_DIR))).expanduser()
        self.status_dir = Path(os.getenv("NNN_UPLOAD_HEALTH_DIR", str(DEFAULT_STATUS_DIR))).expanduser()
        state_file = Path(os.getenv("NNN_UPLOAD_MONITOR_STATE_FILE", str(DEFAULT_STATE_FILE))).expanduser()
        repeat = parse_duration(os.getenv("NNN_UPLOAD_MONITOR_REPEAT_INTERVAL", "6m"), 360.0)
        self.state = State(state_file, self.sender, repeat)
        self.preopen_at = parse_clock(os.getenv("NNN_UPLOAD_MONITOR_PREOPEN_TIME", "09:14:30"), 9 * 3600 + 14 * 60 + 30)
        self.realtime_at = parse_clock(os.getenv("NNN_UPLOAD_MONITOR_REALTIME_TIME", "09:16:30"), 9 * 3600 + 16 * 60 + 30)
        self.public_start = parse_clock(os.getenv("NNN_UPLOAD_MONITOR_PUBLIC_START", "09:20:00"), 9 * 3600 + 20 * 60)
        self.private_start = parse_clock(os.getenv("NNN_UPLOAD_MONITOR_PRIVATE_START", "09:37:00"), 9 * 3600 + 37 * 60)
        self.skip_dates = set(split_csv(os.getenv("NNN_UPLOAD_MONITOR_SKIP_DATES", "")))

    def is_monitor_day(self, now: datetime) -> bool:
        return now.weekday() < 5 and now.date().isoformat() not in self.skip_dates

    def process_problems(
        self,
        strict: bool = True,
        labels: Iterable[str] | None = None,
        now: datetime | None = None,
    ) -> list[Problem]:
        # Packaged mode uses the same freshness/business checks, but its
        # process owner is the Hub component supervisor, not launchd labels.
        owned = os.getenv("MACHOME_UPLOAD_PROCESS_STATE", "")
        if owned:
            try:
                snapshot = json.loads(Path(owned).read_text())
                captured = parse_timestamp(snapshot.get("updated_at"))
                if captured is None or positive_age(now or datetime.now(SHANGHAI), captured) > 15:
                    raise ValueError("stale component supervisor state")
                requested = set(self.labels if labels is None else labels)
                missing = [row["label"] for row in snapshot.get("jobs", [])
                    if row.get("label") in requested and row.get("state") not in ("running", "completed", "scheduled_idle")]
                available = {row.get("label") for row in snapshot.get("jobs", [])}
                missing.extend(sorted(requested - available))
                return [Problem("upload.process.not_running", "upload 包内进程未运行", ", ".join(missing))] if missing else []
            except (OSError, ValueError, TypeError):
                return [Problem("upload.process.not_running", "upload 包内监管状态不可用", "missing or stale supervisor state")]
        missing: list[str] = []
        statuses = self.read_statuses() if strict and now is not None else {}
        for label in self.labels if labels is None else labels:
            target = f"gui/{os.getuid()}/{label}"
            try:
                result = subprocess.run(
                    ["launchctl", "print", target],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=3,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                missing.append(label)
                continue
            if result.returncode != 0:
                missing.append(label)
                continue
            if strict and "state = running" not in result.stdout:
                status = statuses.get(LABEL_SOURCES.get(label, ""))
                last_success = parse_timestamp(status.get("last_success_at")) if status else None
                if last_success is not None and positive_age(now, last_success) <= self.freshness:
                    continue
                missing.append(label)
        if not missing:
            return []
        if strict:
            return [Problem("upload.process.not_running", "upload 进程未运行", ", ".join(missing))]
        return [Problem("upload.process.unmanaged", "upload 进程未由 launchd 托管", ", ".join(missing))]

    def pcf_problems(self, now: datetime) -> tuple[list[Problem], int, int]:
        missing: list[str] = []
        invalid: list[str] = []
        healthy = 0
        expected = 0
        for symbol, template in DEFAULT_PCF_PATHS:
            if symbol in self.ignored_symbols:
                continue
            expected += 1
            relative = template.format(date_dash=now.date().isoformat(), date_compact=now.strftime("%Y%m%d"))
            path = self.runtime_dir / relative
            try:
                payload = path.read_bytes()
                if not payload.strip():
                    raise ValueError("empty")
                if path.suffix.lower() == ".json":
                    json.loads(payload.decode("utf-8"))
                else:
                    ET.fromstring(payload)
            except FileNotFoundError:
                missing.append(symbol)
            except (OSError, UnicodeDecodeError, ValueError, ET.ParseError):
                invalid.append(symbol)
            else:
                healthy += 1
        problems: list[Problem] = []
        if missing:
            problems.append(Problem("upload.pcf.missing", "当日 PCF 文件缺失", ", ".join(missing)))
        if invalid:
            problems.append(Problem("upload.pcf.invalid", "当日 PCF 文件无效", ", ".join(invalid)))
        return problems, healthy, expected

    def tws_problems(self) -> list[Problem]:
        failed: list[str] = []
        for endpoint in self.tws_endpoints:
            host, separator, raw_port = endpoint.rpartition(":")
            if not separator:
                failed.append(endpoint + "(invalid)")
                continue
            try:
                with socket.create_connection((host, int(raw_port)), timeout=2):
                    pass
            except (OSError, ValueError):
                failed.append(endpoint)
        if not failed:
            return []
        return [Problem("upload.tws.connection", "TWS 接口不可达", ", ".join(failed))]

    def read_statuses(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        try:
            paths = tuple(self.status_dir.glob("*.json"))
        except OSError:
            return result
        for path in paths:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            if isinstance(value, dict) and str(value.get("source") or "").strip():
                result[str(value["source"]).strip()] = value
        return result

    def source_problems(self, now: datetime, expected: Iterable[str], key_prefix: str = "upload.source") -> tuple[list[Problem], int, int, float]:
        statuses = self.read_statuses()
        problems: list[Problem] = []
        healthy = 0
        maximum_age = 0.0
        expected_values = tuple(
            source for source in expected if source != SILVER_SOURCE or is_silver_collection_window(now)
        )
        for source in expected_values:
            source_key = key_prefix + "." + source.lower()
            status = statuses.get(source)
            if status is None:
                problems.append(Problem(source_key + ".missing", "upload 健康状态缺失", source))
                continue
            last_success = parse_timestamp(status.get("last_success_at"))
            age = positive_age(now, last_success)
            maximum_age = max(maximum_age, age)
            if age <= self.freshness:
                required_symbols = EXPECTED_SOURCE_SYMBOLS.get(source)
                raw_symbols = status.get("symbols") or ()
                if isinstance(raw_symbols, str):
                    raw_symbols = (raw_symbols,)
                actual_symbols = {
                    str(symbol).strip().upper()
                    for symbol in raw_symbols
                    if str(symbol).strip()
                }
                missing_symbols = sorted(required_symbols - actual_symbols) if required_symbols is not None else []
                if missing_symbols:
                    problems.append(
                        Problem(
                            source_key + ".coverage",
                            "upload 批次缺少预期基金",
                            f"{source}: missing={','.join(missing_symbols)} accepted={len(actual_symbols)}/{len(required_symbols)}",
                        )
                    )
                else:
                    healthy += 1
            elif status.get("state") == "error":
                error = str(status.get("last_error") or "unknown")[:180]
                problems.append(Problem(source_key + ".error", "upload 最近一次执行失败", f"{source}({error})"))
            else:
                problems.append(
                    Problem(source_key + ".stale", "upload 成功时间过期", f"{source}({compact_seconds(age)})")
                )
        return problems, healthy, len(expected_values), maximum_age

    def report(self, now: datetime, name: str, problems: list[Problem], metrics: Iterable[str]) -> str:
        status = "正常" if not problems else "异常"
        lines = [
            f"## Mac-home Upload {name}：{status}",
            "",
            f"- 检查时间：{now.isoformat(timespec='seconds')}",
        ]
        lines.extend(f"- {metric}" for metric in metrics)
        if problems:
            lines.extend(("", "### 未通过项目"))
            lines.extend(f"- {item.title}：{item.detail}" for item in problems)
        else:
            lines.extend(("", "所有 upload 门禁条件均已通过。"))
        return "\n".join(lines)

    def tick(self, now: datetime) -> None:
        if not self.is_monitor_day(now):
            self.state.pause("upload.", now)
            return
        second = seconds_of_day(now)
        process_window = self.private_start <= second < PRIVATE_MONITOR_END
        tws_window = self.public_start <= second < PRIVATE_MONITOR_END
        pcf_window = self.preopen_at <= second < PRIVATE_MONITOR_END
        public_source_window = (
            self.public_start <= second < PUBLIC_MONITOR_END
            and not is_domestic_lunch_monitor_pause(now)
        )
        private_source_window = self.private_start <= second < PRIVATE_MONITOR_END

        active_labels = tuple(
            label
            for label in self.labels
            if (label != PUBLIC_LABEL or second < PUBLIC_MONITOR_END)
            and (label != NIKKEI_LABEL or second < NIKKEI_MONITOR_END)
        )
        active_private_sources = tuple(
            source
            for source in self.private_sources
            if source != NIKKEI_SOURCE or second < NIKKEI_MONITOR_END
        )

        process = self.process_problems(strict=True, labels=active_labels, now=now) if process_window else []
        tws = self.tws_problems() if tws_window else []
        pcf: list[Problem] = []
        pcf_healthy = 0
        pcf_expected = sum(1 for symbol, _ in DEFAULT_PCF_PATHS if symbol not in self.ignored_symbols)
        if pcf_window:
            pcf, pcf_healthy, pcf_expected = self.pcf_problems(now)
        if process_window:
            self.state.reconcile("upload.process.", process, now)
        else:
            self.state.pause("upload.process.", now)
        if tws_window:
            self.state.reconcile("upload.tws.", tws, now)
        else:
            self.state.pause("upload.tws.", now)
        if pcf_window:
            self.state.reconcile("upload.pcf.", pcf, now)
        if public_source_window:
            public_source, _, _, _ = self.source_problems(now, self.public_sources, "upload.public")
            self.state.reconcile("upload.public.", public_source, now)
        else:
            self.state.pause("upload.public.", now)
        if private_source_window:
            private_source, _, _, _ = self.source_problems(now, active_private_sources, "upload.private")
            paused_sources = [source for source in self.private_sources if source not in active_private_sources
                              or (source == SILVER_SOURCE and not is_silver_collection_window(now))]
            self.state.reconcile("upload.private.", private_source, now,
                                 ["upload.private." + source.lower() + "." for source in paused_sources])
        else:
            self.state.pause("upload.private.", now)

        if self.preopen_at <= second < 9 * 3600 + 30 * 60:
            managed = self.process_problems(strict=False)
            scheduled_tws = self.tws_problems()
            problems = managed + scheduled_tws + pcf
            title = "【NAVNAV Upload】盘前上传链路正常" if not problems else "【NAVNAV Upload】盘前上传链路异常"
            self.state.send_once(
                "upload-preopen-" + now.date().isoformat(),
                title,
                self.report(
                    now,
                    "盘前检查",
                    problems,
                    (
                        f"当日 PCF：{pcf_healthy}/{pcf_expected}",
                        f"launchd 托管：{len(self.labels)} 个进程",
                        f"TWS 端点：{', '.join(self.tws_endpoints)}",
                    ),
                ),
                now,
            )
        if self.realtime_at <= second < 9 * 3600 + 30 * 60:
            managed = self.process_problems(strict=False)
            scheduled_tws = self.tws_problems()
            source, healthy, expected, maximum_age = self.source_problems(now, self.public_sources, "upload.public")
            problems = managed + scheduled_tws + pcf + source
            title = "【NAVNAV Upload】09:16 实时上传正常" if not problems else "【NAVNAV Upload】09:16 实时上传异常"
            self.state.send_once(
                "upload-realtime-" + now.date().isoformat(),
                title,
                self.report(
                    now,
                    "09:16 实时检查",
                    problems,
                    (
                        f"公共 upload 数据源：{healthy}/{expected}",
                        f"公共 upload 最大成功年龄：{compact_seconds(maximum_age)}",
                        f"当日 PCF：{pcf_healthy}/{pcf_expected}",
                        f"launchd 托管：{len(self.labels)} 个进程",
                        f"TWS 端点：{', '.join(self.tws_endpoints)}",
                    ),
                ),
                now,
            )

    def run(self, once: bool = False) -> None:
        if not self.enabled:
            print("upload health monitor disabled by PUSHPLUS_MONITOR_ENABLED", flush=True)
            if once:
                return
            while True:
                time.sleep(3600)
        if not self.sender.token:
            raise RuntimeError("PUSHPLUS_TOKEN is not configured")
        if not once and self.startup_grace > 0:
            print(f"upload health monitor warming up for {compact_seconds(self.startup_grace)}", flush=True)
            time.sleep(self.startup_grace)
        while True:
            started = time.monotonic()
            now = datetime.now(SHANGHAI)
            try:
                self.tick(now)
            except Exception as exc:
                print(f"{now.isoformat()} upload monitor tick failed: {exc}", file=sys.stderr, flush=True)
            if once:
                return
            time.sleep(max(0.1, self.interval - (time.monotonic() - started)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    Monitor().run(once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
