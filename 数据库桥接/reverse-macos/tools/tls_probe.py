#!/usr/bin/env python3
"""Probe TGW TLS compatibility without sending WebSocket or credentials."""
from __future__ import annotations

import configparser
import socket
import ssl
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src_reconstructed" / "python"))

from tgw_macos._backend import _find_ca_file  # noqa: E402


def load_endpoint() -> tuple[str, int]:
    parser = configparser.ConfigParser()
    parser.read(ROOT / "config" / "galaxy_account.ini")
    raw = parser["galaxy"]["host"].split("#", 1)[0].strip()
    host = raw.replace("，", " ").replace(",", " ").split()[0]
    return host, parser["galaxy"].getint("port")


def context_for(mode: str) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=_find_ca_file())
    context.check_hostname = False
    if mode == "tls12":
        context.maximum_version = ssl.TLSVersion.TLSv1_2
    elif mode == "compatible":
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        context.set_ciphers("DEFAULT:@SECLEVEL=0")
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    elif mode == "legacy-default":
        context.minimum_version = ssl.TLSVersion.TLSv1
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        context.set_ciphers("DEFAULT:@SECLEVEL=0")
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    elif mode == "legacy-all":
        context.minimum_version = ssl.TLSVersion.TLSv1
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        context.set_ciphers("ALL:@SECLEVEL=0")
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


def main() -> int:
    host, port = load_endpoint()
    successes = 0
    for mode in ("default", "tls12", "compatible", "legacy-default", "legacy-all"):
        try:
            raw = socket.create_connection((host, port), timeout=8)
            with context_for(mode).wrap_socket(raw, server_hostname=None) as tls:
                certificate = tls.getpeercert()
                sans = [value for kind, value in certificate.get("subjectAltName", ()) if kind == "DNS"]
                print(
                    f"{mode}: OK protocol={tls.version()} cipher={tls.cipher()[0]} "
                    f"dns_names={sans}"
                )
                successes += 1
        except Exception as exc:
            print(f"{mode}: FAIL {type(exc).__name__}: {exc}")
    return 0 if successes else 1


if __name__ == "__main__":
    raise SystemExit(main())
