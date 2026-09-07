#!/usr/bin/env python3
"""Strict local client for the Machome native IBKR quote fan-out.

This module intentionally has no HTTP/WebSocket upload code and no ib_insync
dependency.  It talks only to an owner-only Unix domain socket created by
machome-ibkr-bridge.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import threading
import time
from typing import Any, Iterable, Mapping


PROTOCOL = "machome.ibkr.quote.v1"
DEFAULT_SOCKET_PATH = "~/Library/Application Support/MachomeHub/runtime/ibkr-quotes.sock"
MAX_RESPONSE_BYTES = 1024 * 1024
MARKET_DATA_TYPE_NAMES = {
    1: "Live",
    2: "Frozen",
    3: "Delayed",
    4: "DelayedFrozen",
}


class BridgeError(RuntimeError):
    """Base error returned by the local bridge."""


class BridgeUnavailable(BridgeError):
    """The socket or its TWS session is unavailable."""


class BridgeProtocolError(BridgeError):
    """The peer violated the pinned v1 protocol."""


@dataclass(frozen=True)
class ContractSubscription:
    subscription_id: str
    symbol: str
    security_type: str
    exchange: str
    currency: str
    con_id: int = 0
    primary_exchange: str = ""
    expiry: str = ""
    multiplier: str = ""
    trading_class: str = ""
    generic_ticks: str = ""

    @classmethod
    def create(
        cls,
        *,
        symbol: str,
        security_type: str,
        exchange: str,
        currency: str,
        subscription_id: str = "",
        con_id: int = 0,
        primary_exchange: str = "",
        expiry: str = "",
        multiplier: str = "",
        trading_class: str = "",
        generic_ticks: str = "",
    ) -> "ContractSubscription":
        normalized = {
            "symbol": str(symbol).strip().upper(),
            "security_type": str(security_type).strip().upper(),
            "exchange": str(exchange).strip().upper(),
            "currency": str(currency).strip().upper(),
            "con_id": int(con_id or 0),
            "primary_exchange": str(primary_exchange).strip().upper(),
            "expiry": str(expiry).strip(),
            "multiplier": str(multiplier).strip(),
            "trading_class": str(trading_class).strip().upper(),
            "generic_ticks": str(generic_ticks).strip(),
        }
        if not subscription_id:
            identity = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
            subscription_id = (
                f"DYN.{normalized['security_type']}.{normalized['symbol']}.{digest}"
            )
        result = cls(subscription_id=str(subscription_id).strip(), **normalized)
        result.validate()
        return result

    def validate(self) -> None:
        code = re.compile(r"^[A-Za-z0-9._:/ -]+$")
        for name, value, maximum in (
            ("subscription_id", self.subscription_id, 128),
            ("symbol", self.symbol, 32),
            ("security_type", self.security_type, 16),
            ("exchange", self.exchange, 32),
            ("currency", self.currency, 8),
        ):
            if not value or len(value) > maximum or not code.fullmatch(value):
                raise ValueError(f"{name} is empty, too long, or invalid")
        for name, value, maximum in (
            ("primary_exchange", self.primary_exchange, 32),
            ("expiry", self.expiry, 16),
            ("multiplier", self.multiplier, 16),
            ("trading_class", self.trading_class, 32),
        ):
            if value and (len(value) > maximum or not code.fullmatch(value)):
                raise ValueError(f"{name} is too long or invalid")
        if self.con_id < 0 or self.con_id > 2_000_000_000:
            raise ValueError("con_id is outside 0..2000000000")
        if len(self.generic_ticks) > 128 or not re.fullmatch(r"[0-9,]*", self.generic_ticks):
            raise ValueError("generic_ticks must contain only digits and commas")

    def contract_payload(self) -> dict[str, Any]:
        return {
            "con_id": self.con_id,
            "symbol": self.symbol,
            "security_type": self.security_type,
            "exchange": self.exchange,
            "primary_exchange": self.primary_exchange,
            "currency": self.currency,
            "expiry": self.expiry,
            "multiplier": self.multiplier,
            "trading_class": self.trading_class,
            "generic_ticks": self.generic_ticks,
        }


@dataclass(frozen=True)
class BridgeQuote:
    subscription_id: str
    symbol: str
    bid: float
    ask: float
    last: float | None
    close: float | None
    bid_size: str | None
    ask_size: str | None
    last_size: str | None
    market_data_type_id: int
    market_data_type: str
    observed_at: datetime
    exchange_timestamp: datetime | None
    sequence: int
    age_ms: int

    @staticmethod
    def _optional_positive(value: Any, name: str) -> float | None:
        if value is None:
            return None
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise BridgeProtocolError(f"quote {name} is not numeric") from exc
        if not math.isfinite(result) or result <= 0:
            raise BridgeProtocolError(f"quote {name} must be finite and positive")
        return result

    @staticmethod
    def _timestamp(value: Any, name: str, *, required: bool) -> datetime | None:
        if value is None and not required:
            return None
        if not isinstance(value, str) or not value:
            raise BridgeProtocolError(f"quote {name} is missing")
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise BridgeProtocolError(f"quote {name} is not ISO-8601") from exc
        if result.tzinfo is None:
            raise BridgeProtocolError(f"quote {name} has no timezone")
        return result

    @classmethod
    def from_item(cls, item: Mapping[str, Any], *, require_fresh: bool = True) -> "BridgeQuote | None":
        if not isinstance(item, Mapping):
            raise BridgeProtocolError("quote item is not an object")
        contract = item.get("contract")
        if not isinstance(contract, Mapping):
            raise BridgeProtocolError("quote contract is missing")
        bid = cls._optional_positive(item.get("bid"), "bid")
        ask = cls._optional_positive(item.get("ask"), "ask")
        if bid is None or ask is None:
            return None
        if ask < bid:
            raise BridgeProtocolError("quote ask is below bid")
        fresh = item.get("fresh")
        if not isinstance(fresh, bool):
            raise BridgeProtocolError("quote fresh is not boolean")
        if require_fresh and not fresh:
            raise BridgeUnavailable("bridge quote is stale")
        market_type = item.get("market_data_type")
        if not isinstance(market_type, int) or market_type not in MARKET_DATA_TYPE_NAMES:
            return None
        observed_at = cls._timestamp(item.get("received_at"), "received_at", required=True)
        exchange_timestamp = cls._timestamp(
            item.get("exchange_timestamp"), "exchange_timestamp", required=False
        )
        subscription_id = contract.get("id")
        symbol = contract.get("symbol")
        sequence = item.get("sequence")
        age_ms = item.get("age_ms")
        if not isinstance(subscription_id, str) or not subscription_id:
            raise BridgeProtocolError("quote subscription id is missing")
        if not isinstance(symbol, str) or not symbol:
            raise BridgeProtocolError("quote symbol is missing")
        if not isinstance(sequence, int) or sequence < 0:
            raise BridgeProtocolError("quote sequence is invalid")
        if not isinstance(age_ms, int) or age_ms < 0:
            raise BridgeProtocolError("quote age_ms is invalid")
        return cls(
            subscription_id=subscription_id,
            symbol=symbol,
            bid=bid,
            ask=ask,
            last=cls._optional_positive(item.get("last"), "last"),
            close=cls._optional_positive(item.get("close"), "close"),
            bid_size=item.get("bid_size") if isinstance(item.get("bid_size"), str) else None,
            ask_size=item.get("ask_size") if isinstance(item.get("ask_size"), str) else None,
            last_size=item.get("last_size") if isinstance(item.get("last_size"), str) else None,
            market_data_type_id=market_type,
            market_data_type=MARKET_DATA_TYPE_NAMES[market_type],
            observed_at=observed_at,
            exchange_timestamp=exchange_timestamp,
            sequence=sequence,
            age_ms=age_ms,
        )


class SharedQuoteClient:
    """One synchronous, thread-safe Unix-socket client.

    A client owns leases, not TWS subscriptions.  The native bridge coalesces
    leases with the same subscription id and exact contract identity.
    """

    def __init__(self, socket_path: str, timeout: float = 2.0) -> None:
        self.socket_path = str(Path(socket_path).expanduser())
        self.timeout = float(timeout)
        if self.timeout <= 0 or self.timeout > 120:
            raise ValueError("timeout must be in (0, 120]")
        self._socket: socket.socket | None = None
        self._buffer = bytearray()
        self._lock = threading.RLock()
        self._request_sequence = 0
        self._leases: dict[str, ContractSubscription] = {}

    @classmethod
    def from_environment(cls, timeout: float = 2.0) -> "SharedQuoteClient":
        return cls(os.environ.get("MACHOME_IBKR_BRIDGE_SOCKET", DEFAULT_SOCKET_PATH), timeout)

    @property
    def connected(self) -> bool:
        return self._socket is not None

    @property
    def leases(self) -> tuple[str, ...]:
        return tuple(sorted(self._leases))

    def connect(self) -> None:
        with self._lock:
            self.close()
            peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            peer.settimeout(self.timeout)
            try:
                peer.connect(self.socket_path)
                self._socket = peer
                response = self._roundtrip_locked({"type": "hello", "protocol": PROTOCOL})
                capabilities = response.get("capabilities")
                required = {"status", "quotes", "quote", "subscribe", "unsubscribe", "ping"}
                if not isinstance(capabilities, list) or not required.issubset(set(capabilities)):
                    raise BridgeProtocolError("bridge lacks dynamic subscription capabilities")
            except Exception:
                peer.close()
                self._socket = None
                self._buffer.clear()
                raise

    def close(self) -> None:
        with self._lock:
            peer, self._socket = self._socket, None
            self._buffer.clear()
            self._leases.clear()
            if peer is not None:
                try:
                    peer.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                peer.close()

    def __enter__(self) -> "SharedQuoteClient":
        self.connect()
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()

    def status(self) -> dict[str, Any]:
        return self._request({"type": "status"})

    def is_ready(self) -> bool:
        try:
            status = self.status()
        except BridgeError:
            return False
        return status.get("ready") is True and status.get("tws_connected") is True

    def subscribe(self, subscription: ContractSubscription) -> bool:
        subscription.validate()
        response = self._request({
            "type": "subscribe",
            "subscription_id": subscription.subscription_id,
            "contract": subscription.contract_payload(),
        })
        created = response.get("created")
        if not isinstance(created, bool):
            raise BridgeProtocolError("subscribe response has no created boolean")
        self._leases[subscription.subscription_id] = subscription
        return created

    def unsubscribe(self, subscription_id: str) -> None:
        self._request({"type": "unsubscribe", "subscription_id": subscription_id})
        self._leases.pop(subscription_id, None)

    def quote(self, subscription_id: str, *, require_fresh: bool = True) -> BridgeQuote | None:
        response = self._request({"type": "quote", "subscription_id": subscription_id})
        if response.get("bridge_ready") is not True:
            raise BridgeUnavailable("native bridge TWS session is not ready")
        return BridgeQuote.from_item(response.get("item"), require_fresh=require_fresh)

    def quotes(
        self, subscription_ids: Iterable[str] | None = None, *, require_fresh: bool = True
    ) -> dict[str, BridgeQuote | None]:
        response = self._request({"type": "quotes"})
        if response.get("bridge_ready") is not True:
            raise BridgeUnavailable("native bridge TWS session is not ready")
        items = response.get("items")
        if not isinstance(items, list):
            raise BridgeProtocolError("quotes response items is not an array")
        requested = set(subscription_ids if subscription_ids is not None else self._leases)
        result: dict[str, BridgeQuote | None] = {}
        for item in items:
            contract = item.get("contract") if isinstance(item, Mapping) else None
            item_id = contract.get("id") if isinstance(contract, Mapping) else None
            if isinstance(item_id, str) and item_id in requested:
                try:
                    quote = BridgeQuote.from_item(item, require_fresh=require_fresh)
                except BridgeUnavailable:
                    # A stale symbol is an item-level condition. Keep other
                    # symbols in the same batch usable so one halted market or
                    # thin contract cannot stall the uploader's whole basket.
                    quote = None
                result[item_id] = quote
        missing = requested.difference(result)
        if missing:
            raise BridgeProtocolError("quotes response omitted: " + ", ".join(sorted(missing)))
        return result

    def wait_for_quotes(
        self,
        subscription_ids: Iterable[str] | None = None,
        *,
        timeout: float | None = None,
        poll_interval: float = 0.05,
    ) -> dict[str, BridgeQuote]:
        requested = tuple(subscription_ids if subscription_ids is not None else self._leases)
        deadline = time.monotonic() + (self.timeout if timeout is None else max(0.0, timeout))
        while True:
            values = self.quotes(requested)
            complete = {key: value for key, value in values.items() if value is not None}
            if len(complete) == len(requested):
                return complete
            if time.monotonic() >= deadline:
                return complete
            time.sleep(max(0.0, min(poll_interval, deadline - time.monotonic())))

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._socket is None:
                raise BridgeUnavailable("native bridge socket is not connected")
            try:
                return self._roundtrip_locked(payload)
            except (OSError, socket.timeout) as exc:
                self.close()
                raise BridgeUnavailable(f"native bridge socket failed: {exc}") from exc

    def _roundtrip_locked(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._socket is None:
            raise BridgeUnavailable("native bridge socket is not connected")
        self._request_sequence += 1
        request_id = str(self._request_sequence)
        message = dict(payload)
        message["request_id"] = request_id
        encoded = json.dumps(message, separators=(",", ":"), ensure_ascii=True).encode("utf-8") + b"\n"
        self._socket.sendall(encoded)
        response = self._receive_locked()
        if response.get("protocol") != PROTOCOL:
            raise BridgeProtocolError("bridge protocol mismatch")
        if response.get("request_id") != request_id:
            raise BridgeProtocolError("bridge request_id mismatch")
        if response.get("ok") is not True:
            error = response.get("error")
            code = error.get("code") if isinstance(error, Mapping) else "bridge_error"
            message_text = error.get("message") if isinstance(error, Mapping) else "request failed"
            raise BridgeError(f"{code}: {message_text}")
        return response

    def _receive_locked(self) -> dict[str, Any]:
        if self._socket is None:
            raise BridgeUnavailable("native bridge socket is not connected")
        while b"\n" not in self._buffer:
            chunk = self._socket.recv(64 * 1024)
            if not chunk:
                raise BridgeUnavailable("native bridge closed the socket")
            self._buffer.extend(chunk)
            if len(self._buffer) > MAX_RESPONSE_BYTES:
                raise BridgeProtocolError("bridge response exceeds 1 MiB")
        raw, _, remaining = self._buffer.partition(b"\n")
        self._buffer = bytearray(remaining)
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BridgeProtocolError("bridge response is not UTF-8 JSON") from exc
        if not isinstance(decoded, dict):
            raise BridgeProtocolError("bridge response is not an object")
        return decoded


class SharedSingleQuoteStream:
    """Small compatibility wrapper used by legacy uploader classes."""

    def __init__(self, subscription: ContractSubscription, timeout: float) -> None:
        self.subscription = subscription
        self.client = SharedQuoteClient.from_environment(timeout=timeout)

    def connect(self) -> None:
        self.client.connect()
        self.client.subscribe(self.subscription)

    def is_connected(self) -> bool:
        return self.client.connected and self.client.is_ready()

    def poll(self, wait_seconds: float = 0.05) -> BridgeQuote | None:
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        return self.client.quote(self.subscription.subscription_id)

    def close(self) -> None:
        self.client.close()


class SharedMultiQuoteStream:
    """Batches many uploader symbols into one bridge request per poll."""

    def __init__(self, subscriptions: Iterable[ContractSubscription], timeout: float) -> None:
        values = tuple(subscriptions)
        if not values:
            raise ValueError("at least one subscription is required")
        if len({item.subscription_id for item in values}) != len(values):
            raise ValueError("subscription ids must be unique")
        self.subscriptions = values
        self.client = SharedQuoteClient.from_environment(timeout=timeout)

    def connect(self) -> None:
        self.client.connect()
        try:
            for subscription in self.subscriptions:
                self.client.subscribe(subscription)
        except Exception:
            self.client.close()
            raise

    def is_connected(self) -> bool:
        return self.client.connected and self.client.is_ready()

    def poll(self, wait_seconds: float = 0.05) -> dict[str, BridgeQuote]:
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        ids = tuple(item.subscription_id for item in self.subscriptions)
        return self.client.wait_for_quotes(ids, timeout=0.0)

    def close(self) -> None:
        self.client.close()
