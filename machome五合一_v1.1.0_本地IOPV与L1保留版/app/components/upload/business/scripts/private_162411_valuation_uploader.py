#!/usr/bin/env python3
"""Publish the independent Private SZ162411 XOP-close LOF valuation input.

The public SZ162411 model is the canonical source of the dated official NAV,
the dated 16:00 ET XOP close anchor and the resolved effective exposure.  This
collector adds exact SAFE central parities and the current two-sided TWS XOP
quote.  It deliberately never reads a PCF, CFETS intraday FX or a redemption
basket coefficient.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import private_164824_valuation_uploader as india_common
import private_nasdaq_valuation_uploader as safe_common
import private_valuation_uploader as common


SYMBOL = "SZ162411"
REFERENCE_SYMBOL = "XOP"
PUBLIC_MODEL_VERSION = "v1.weighted_anchor.xop"
MODEL_VERSION = "private.weighted-nav.xop-safe.us-close.v1"
VALUATION_KIND = "lof_weighted_anchor"
DEFAULT_EFFECTIVE_RATIO = 0.955
DEFAULT_SOURCE = "mac-home-private-162411-lof-uploader"
DEFAULT_UPLOAD_INTERVAL = 3.0
DEFAULT_SEED_INTERVAL = 15.0 * 60.0
DEFAULT_FX_INTERVAL = 60.0
DEFAULT_TIMEOUT = 12.0
SHANGHAI = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")


class SourceUnavailableError(RuntimeError):
    """A mandatory audited source is missing or internally inconsistent."""


@dataclass(frozen=True)
class BaseReference:
    price: float
    anchor_day: date
    target_at: datetime
    observed_at: datetime
    source: str
    capture_status: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "symbol": REFERENCE_SYMBOL,
            "price": self.price,
            "price_basis": "regular_session_close",
            "target_at": common.iso_timestamp(self.target_at),
            "observed_at": common.iso_timestamp(self.observed_at),
            "source": self.source,
            "capture_status": self.capture_status,
        }


@dataclass(frozen=True)
class PublicModelSeed:
    base_nav: float
    base_nav_date: date
    base_nav_source: str
    fetched_at: datetime
    base_reference: BaseReference
    effective_ratio: float
    ratio_source: str


def positive(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SourceUnavailableError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number <= 0:
        raise SourceUnavailableError(f"{field} must be a positive finite number")
    return number


def parse_timestamp(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise SourceUnavailableError(f"{field} is required")
    # Go emits RFC3339Nano (up to nine fractional digits), while the mac-home
    # Python 3.10 runtime accepts at most microseconds in fromisoformat().
    text = re.sub(
        r"(\.\d{6})\d+(?=(?:Z|[+-]\d{2}:\d{2})$)",
        r"\1",
        text,
    )
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceUnavailableError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise SourceUnavailableError(f"{field} must carry a timezone")
    return parsed


def parse_public_seed(payload: Any, fetched_at: datetime) -> PublicModelSeed:
    if not isinstance(payload, dict) or str(payload.get("symbol") or "").upper() != SYMBOL:
        raise SourceUnavailableError("public fund snapshot is not SZ162411")
    estimate = payload.get("estimate")
    inputs = payload.get("valuation_inputs")
    if not isinstance(estimate, dict) or not isinstance(inputs, list):
        raise SourceUnavailableError("public fund snapshot has no estimate or valuation_inputs")
    if str(estimate.get("model_version") or "") != PUBLIC_MODEL_VERSION:
        raise SourceUnavailableError("public SZ162411 model_version is not the approved XOP weighted-anchor model")
    if str(estimate.get("reference_symbol") or "").upper() != REFERENCE_SYMBOL:
        raise SourceUnavailableError("public SZ162411 reference symbol is not XOP")

    nav_rows = [
        item for item in inputs
        if isinstance(item, dict)
        and item.get("role") == "净值基准"
        and str(item.get("symbol") or "").upper() == SYMBOL
    ]
    anchor_rows = [
        item for item in inputs
        if isinstance(item, dict)
        and item.get("role") == "估值锚点"
        and str(item.get("symbol") or "").upper() == REFERENCE_SYMBOL
        and item.get("name") == "美国收盘"
    ]
    if len(nav_rows) != 1 or len(anchor_rows) != 1:
        raise SourceUnavailableError("public SZ162411 snapshot must contain one base NAV and one XOP US-close anchor")

    nav_row = nav_rows[0]
    anchor_row = anchor_rows[0]
    base_day = common.parse_date(nav_row.get("base_date"), "base NAV date")
    anchor_day = common.parse_date(anchor_row.get("base_date"), "XOP anchor date")
    if anchor_day != base_day:
        raise SourceUnavailableError("public XOP anchor date must equal the base NAV date")
    # Actual event audit is mandatory.  Falling back to the requested target
    # would make an old/mis-timed price look like a true close and defeats the
    # purpose of this independent Private trace.
    public_target = parse_timestamp(anchor_row.get("target_at"), "XOP anchor target_at")
    observed_at = parse_timestamp(anchor_row.get("observed_at"), "XOP anchor observed_at")
    target_local = public_target.astimezone(NEW_YORK)
    carry_days = (base_day - target_local.date()).days
    if (
        target_local.hour != 16
        or target_local.minute != 0
        or target_local.second != 0
        or carry_days < 0
        or carry_days > 7
    ):
        raise SourceUnavailableError(
            "public XOP anchor target_at must be a 16:00 America/New_York close on base date or within the prior seven days"
        )
    target = public_target
    anchor_day = target_local.date()
    if abs((observed_at - target).total_seconds()) > 180:
        raise SourceUnavailableError(
            "public XOP anchor observed_at must be within 180 seconds of target_at"
        )
    snapshot_at = parse_timestamp(payload.get("as_of"), "public snapshot as_of")
    ratio = positive(estimate.get("effective_ratio"), "effective ratio")
    if ratio > 1:
        raise SourceUnavailableError("SZ162411 effective ratio must not exceed 1")
    # The public snapshot exposes the resolved value but not the operator/time
    # audit required for a Private manual override.  Accept the configured
    # weighted-anchor default only; a changed public ratio must fail closed
    # until its override metadata is exposed rather than being guessed here.
    if not math.isclose(ratio, DEFAULT_EFFECTIVE_RATIO, rel_tol=0.0, abs_tol=1e-12):
        raise SourceUnavailableError(
            "public SZ162411 effective ratio differs from 0.955 but exposes no override audit"
        )
    ratio_source = "weighted_anchor"
    anchor_source = str(anchor_row.get("base_source") or "").strip()
    nav_source = str(nav_row.get("base_source") or "").strip()
    if not anchor_source or not nav_source:
        raise SourceUnavailableError("public base NAV and XOP anchor must identify their sources")
    capture_status = str(anchor_row.get("capture_status") or "").strip()
    if not capture_status:
        raise SourceUnavailableError("public XOP anchor capture_status is empty")
    return PublicModelSeed(
        base_nav=positive(nav_row.get("base_price"), "base NAV"),
        base_nav_date=base_day,
        base_nav_source=nav_source,
        fetched_at=snapshot_at if snapshot_at <= fetched_at else fetched_at,
        base_reference=BaseReference(
            price=positive(anchor_row.get("base_price"), "base XOP close"),
            anchor_day=anchor_day,
            target_at=target,
            observed_at=observed_at,
            source=anchor_source,
            capture_status=capture_status,
        ),
        effective_ratio=ratio,
        ratio_source=ratio_source,
    )


def fetch_public_seed(args: argparse.Namespace, fetched_at: datetime) -> PublicModelSeed:
    request = urllib.request.Request(
        args.public_server.rstrip("/") + "/api/v1/funds/" + SYMBOL,
        headers={
            "Accept": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": common.CHROME_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise SourceUnavailableError(f"public SZ162411 snapshot unavailable: {exc}") from exc
    return parse_public_seed(payload, fetched_at)


def safe_payload(value: Any) -> dict[str, Any]:
    return india_common.safe_payload(value)


def build_payload(
    seed: PublicModelSeed,
    base_fx: Any,
    current_fx: Any,
    quote: common.IBQuote,
    generated_at: datetime,
    source: str = DEFAULT_SOURCE,
    *,
    contract: str = "XOP STK SMART/ARCA conId=413951498",
    quote_session: str = "us_smart_live",
) -> dict[str, Any]:
    if base_fx.trading_day != seed.base_nav_date:
        raise SourceUnavailableError("base SAFE parity day must equal the base NAV date")
    local_day = generated_at.astimezone(SHANGHAI).date()
    if current_fx.trading_day != local_day:
        raise SourceUnavailableError("current SAFE parity must be exact for the current Shanghai day")
    if quote.ask < quote.bid:
        raise SourceUnavailableError("current XOP ask must not be below bid")
    if not str(contract or "").strip() or not str(quote_session or "").strip():
        raise SourceUnavailableError("XOP contract and quote_session audit labels are required")
    ib = quote.to_payload()
    ib["contract"] = contract
    ib["quote_session"] = quote_session
    return {
        "schema_version": 1,
        "symbol": SYMBOL,
        "model_version": MODEL_VERSION,
        "valuation_kind": VALUATION_KIND,
        "lof": {
            "base_nav": seed.base_nav,
            "base_nav_date": seed.base_nav_date.isoformat(),
            "base_nav_source": seed.base_nav_source,
            "base_nav_fetched_at": common.iso_timestamp(seed.fetched_at),
            "base_reference": seed.base_reference.to_payload(),
            "base_fx": safe_payload(base_fx),
            "current_fx": safe_payload(current_fx),
            "effective_ratio": {
                "value": seed.effective_ratio,
                "source": seed.ratio_source,
                "default_value": DEFAULT_EFFECTIVE_RATIO,
                "default_source": "weighted_anchor",
            },
        },
        "ib": ib,
        "source": source,
        "generated_at": common.iso_timestamp(generated_at),
    }


def post(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    request = urllib.request.Request(
        args.server.rstrip("/") + "/api/v1/private/inputs/" + SYMBOL,
        data=common.gzip_json_body(payload),
        method="POST",
        headers=common.gzip_server_headers(args.server, args.token),
    )
    try:
        with common.open_server_request(
            request,
            args.timeout,
            args.origin_ip,
            args.origin_tls_insecure,
            args.origin_ca_file,
        ) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SourceUnavailableError(f"private SZ162411 upload status {exc.code}: {detail}") from exc
    if body:
        try:
            reply = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise SourceUnavailableError("private SZ162411 upload returned invalid JSON") from exc
        if isinstance(reply, dict) and reply.get("error"):
            raise SourceUnavailableError(str(reply["error"]))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "https://1navs.com"))
    result.add_argument("--public-server", default=os.getenv("NNN_PRIVATE_162411_PUBLIC_SERVER", "https://1navs.com"))
    result.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    result.add_argument("--origin-ip", default=os.getenv("NNN_ORIGIN_IP", ""))
    result.add_argument("--origin-ca-file", default=os.getenv("NNN_ORIGIN_CA_FILE", ""))
    result.add_argument(
        "--origin-tls-insecure",
        action="store_true",
        default=common.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", "")),
    )
    result.add_argument("--ib-host", default=os.getenv("NNN_PRIVATE_162411_IB_HOST", os.getenv("NNN_IB_HOST", "127.0.0.1")))
    result.add_argument("--ib-port", type=int, default=int(os.getenv("NNN_PRIVATE_162411_IB_PORT", os.getenv("NNN_IB_PORT", "7496"))))
    result.add_argument("--ib-client-id", type=int, default=int(os.getenv("NNN_PRIVATE_162411_IB_CLIENT_ID", "162411")))
    result.add_argument("--timeout", type=float, default=float(os.getenv("NNN_UPLOAD_TIMEOUT", str(DEFAULT_TIMEOUT))))
    result.add_argument("--upload-interval", type=float, default=float(os.getenv("NNN_PRIVATE_162411_UPLOAD_INTERVAL", str(DEFAULT_UPLOAD_INTERVAL))))
    result.add_argument("--seed-interval", type=float, default=float(os.getenv("NNN_PRIVATE_162411_SEED_INTERVAL", str(DEFAULT_SEED_INTERVAL))))
    result.add_argument("--fx-interval", type=float, default=float(os.getenv("NNN_PRIVATE_162411_FX_INTERVAL", str(DEFAULT_FX_INTERVAL))))
    result.add_argument("--source", default=os.getenv("NNN_PRIVATE_162411_SOURCE", DEFAULT_SOURCE))
    result.add_argument("--once", action="store_true")
    result.add_argument("--force", action="store_true", help="allow one diagnostic upload outside the China session")
    return result


def run(args: argparse.Namespace) -> int:
    if not str(args.token or "").strip():
        raise SourceUnavailableError("NNN_UPLOAD_TOKEN or --token is required")
    stream = common.IBQuoteStream(args.ib_host, args.ib_port, args.ib_client_id, args.timeout)
    seed: PublicModelSeed | None = None
    base_fx: Any = None
    current_fx: Any = None
    next_seed_refresh = next_fx_refresh = 0.0
    try:
        while not common.STOP_EVENT.is_set():
            now = datetime.now(SHANGHAI)
            if not args.force and not common.ib_collection_window(now):
                stream.disconnect()
                if args.once:
                    return 0
                common.STOP_EVENT.wait(30.0)
                continue
            if not stream.is_connected():
                stream.connect()
            monotonic_now = time.monotonic()
            if monotonic_now >= next_seed_refresh:
                seed = fetch_public_seed(args, now)
                base_fx = safe_common.fetch_safe_central_parity(
                    seed.base_nav_date, args.timeout, safe_common.NASDAQ_FAMILY
                )
                next_seed_refresh = monotonic_now + max(60.0, args.seed_interval)
            if (
                monotonic_now >= next_fx_refresh
                and now.hour * 60 + now.minute >= 9 * 60 + 15
            ):
                current_fx = safe_common.fetch_safe_central_parity(
                    now.date(), args.timeout, safe_common.NASDAQ_FAMILY
                )
                next_fx_refresh = monotonic_now + max(30.0, args.fx_interval)
            quote = stream.poll(0.1)
            if quote is None:
                if args.once:
                    common.STOP_EVENT.wait(0.2)
                continue
            if seed is None or base_fx is None or current_fx is None:
                raise SourceUnavailableError("SZ162411 needs public base data, SAFE parities and current XOP")
            payload = build_payload(seed, base_fx, current_fx, quote, now, args.source)
            post(args, payload)
            common.log(
                "162411 private LOF input uploaded "
                f"base_nav={seed.base_nav:.4f}@{seed.base_nav_date} "
                f"XOP-anchor={seed.base_reference.price:.4f} "
                f"XOP={quote.bid:.4f}/{quote.ask:.4f} "
                f"SAFE={base_fx.rate:.4f}/{current_fx.rate:.4f} "
                f"ratio={seed.effective_ratio:.4f}"
            )
            if args.once:
                return 0
            common.STOP_EVENT.wait(max(0.5, args.upload_interval))
    finally:
        stream.disconnect()
    return 0


def main() -> int:
    common.load_env_file(".sina-uploader.env")
    args = parser().parse_args()
    signal.signal(signal.SIGINT, common._request_stop)
    signal.signal(signal.SIGTERM, common._request_stop)
    try:
        return run(args)
    except (SourceUnavailableError, safe_common.SourceUnavailableError, common.SourceUnavailableError, RuntimeError, urllib.error.URLError) as exc:
        common.log(str(exc), error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
