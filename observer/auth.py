# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Entra Easy Auth and HMAC-SHA256 request authentication for the outside observer."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time


def extract_entra_principal(headers: dict[str, str]) -> str:
    """Extracts the Entra object ID from Azure Functions / App Service Easy Auth headers."""
    lowered = {k.lower(): v for k, v in headers.items()}
    encoded = lowered.get("x-ms-client-principal", "").strip()
    if encoded:
        try:
            decoded = json.loads(base64.b64decode(encoded).decode("utf-8"))
            if isinstance(decoded, dict):
                oid = str(decoded.get("userId") or decoded.get("oid") or "").strip()
                if oid:
                    return oid
        except Exception:
            pass
    return lowered.get("x-ms-client-principal-id", "").strip()


def compute_hmac_signature(raw_body: bytes, timestamp: str, secret: str) -> str:
    """Computes `sha256=<hex>` over `<timestamp>.<body>`."""
    payload = f"{timestamp}.".encode("utf-8") + raw_body
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_hmac_signature(
    raw_body: bytes,
    signature_header: str,
    timestamp_header: str,
    secret: str,
    *,
    now_ts: float | None = None,
    max_skew_seconds: int = 300,
) -> tuple[bool, str]:
    """Verifies an HMAC-SHA256 signature and timestamp replay window."""
    if not secret:
        return False, "auth: hmac secret not configured"
    sig = signature_header.strip()
    ts_str = timestamp_header.strip()
    if not sig or not ts_str:
        return False, "auth: missing signature or timestamp header"
    try:
        ts_val = float(ts_str)
    except ValueError:
        return False, "auth: invalid timestamp header"

    current = time.time() if now_ts is None else now_ts
    if abs(current - ts_val) > max_skew_seconds:
        return False, "auth: timestamp outside replay window"

    expected = compute_hmac_signature(raw_body, ts_str, secret)
    if not hmac.compare_digest(sig.lower(), expected.lower()):
        return False, "auth: signature mismatch"
    return True, "ok"


def authenticate_request(
    headers: dict[str, str],
    raw_body: bytes,
    *,
    hmac_secret: str = "",
    require_auth: bool = True,
    now_ts: float | None = None,
) -> tuple[bool, str]:
    """Authenticates an inbound HTTP request via Entra Easy Auth or HMAC-SHA256."""
    if not require_auth:
        return True, "auth-disabled"

    principal = extract_entra_principal(headers)
    if principal:
        return True, f"entra:{principal}"

    lowered = {k.lower(): v for k, v in headers.items()}
    sig = lowered.get("x-observer-signature", "")
    ts = lowered.get("x-observer-timestamp", "")
    if hmac_secret and (sig or ts):
        ok, reason = verify_hmac_signature(raw_body, sig, ts, hmac_secret, now_ts=now_ts)
        if ok:
            return True, "hmac"
        return False, reason

    return False, "auth: missing Entra principal or valid HMAC signature"
