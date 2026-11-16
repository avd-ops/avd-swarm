# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Signed co-signature verification for avd_gate (docs/notifications.md section 5; decision 0011).

Binds (planId, fullPlanHash, signerOid, channel, issuedAt, expiresAt) against an authorized
signerOid allowlist and channel identity. Never accepts static unauthenticated webhooks or
8-character display hash prefixes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

_FULL_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_AUTH_METHODS = frozenset(
    {"", "none", "static_webhook", "static-webhook", "unauthenticated", "webhook"}
)


def _parse_iso_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def canonical_cosign_message(
    *,
    plan_id: str,
    full_plan_hash: str,
    signer_oid: str,
    channel: str,
    issued_at: str,
    expires_at: str,
) -> bytes:
    """Returns the canonical UTF-8 JSON bytes signed by an external co-signer."""
    payload = {
        "planId": plan_id,
        "fullPlanHash": full_plan_hash,
        "signerOid": signer_oid,
        "channel": channel,
        "issuedAt": issued_at,
        "expiresAt": expires_at,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_cosignature(
    *,
    plan_id: str,
    full_plan_hash: str,
    signer_oid: str,
    channel: str,
    issued_at: str,
    expires_at: str,
    channel_key: bytes | str,
    auth_method: str = "hmac-sha256",
) -> dict[str, str]:
    """Creates a signed co-signature envelope bound to the full SHA-256 planHash."""
    key_bytes = channel_key.encode("utf-8") if isinstance(channel_key, str) else channel_key
    msg = canonical_cosign_message(
        plan_id=plan_id,
        full_plan_hash=full_plan_hash,
        signer_oid=signer_oid,
        channel=channel,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    sig = hmac.new(key_bytes, msg, hashlib.sha256).hexdigest()
    return {
        "planId": plan_id,
        "fullPlanHash": full_plan_hash,
        "signerOid": signer_oid,
        "channel": channel,
        "channelIdentity": channel,
        "issuedAt": issued_at,
        "expiresAt": expires_at,
        "authMethod": auth_method,
        "signature": sig,
    }


def verify_cosignature(
    cosignature: object,
    *,
    expected_plan_id: str,
    expected_full_plan_hash: str,
    authorized_signer_oids: set[str] | list[str] | tuple[str, ...],
    allowed_channels: set[str] | list[str] | tuple[str, ...] = ("teams", "servicenow"),
    channel_keys: dict[str, bytes | str] | None = None,
    now_utc: datetime | None = None,
    max_ttl_minutes: int = 60,
) -> list[str]:
    """Verifies a signed co-signature. Returns a list of refusal strings (empty means valid)."""
    if not isinstance(cosignature, dict):
        return ["cosign: missing or invalid co-signature object"]

    auth_method = str(cosignature.get("authMethod") or "").strip().lower()
    if auth_method in FORBIDDEN_AUTH_METHODS:
        return [f"cosign: authMethod {auth_method or 'empty'!r} is not allowed (static/unauthenticated webhooks rejected)"]

    plan_id = str(cosignature.get("planId") or "").strip()
    if not plan_id or plan_id != expected_plan_id:
        return ["cosign: planId mismatch"]

    full_hash = str(cosignature.get("fullPlanHash") or "").strip().lower()
    if not _FULL_SHA256_RE.match(full_hash):
        return ["cosign: fullPlanHash must be a full 64-char SHA-256 hex digest (8-char prefix rejected)"]
    if not _FULL_SHA256_RE.match(expected_full_plan_hash.lower()) or not hmac.compare_digest(
        full_hash, expected_full_plan_hash.lower()
    ):
        return ["cosign: fullPlanHash does not match current canonical planHash"]

    signer_oid = str(cosignature.get("signerOid") or "").strip()
    allowlist = {str(o).strip() for o in authorized_signer_oids if str(o).strip()}
    if not signer_oid or signer_oid not in allowlist:
        return ["cosign: signerOid is not in authorized allowlist"]

    channel = str(cosignature.get("channel") or "").strip().lower()
    allowed_ch = {str(c).strip().lower() for c in allowed_channels if str(c).strip()}
    if not channel or channel not in allowed_ch:
        return [f"cosign: channel {channel!r} is not allowed"]

    channel_identity = str(cosignature.get("channelIdentity") or channel).strip().lower()
    if channel_identity != channel:
        return ["cosign: channelIdentity does not match channel"]

    issued_raw = str(cosignature.get("issuedAt") or "").strip()
    expires_raw = str(cosignature.get("expiresAt") or "").strip()
    issued_dt = _parse_iso_utc(issued_raw)
    expires_dt = _parse_iso_utc(expires_raw)
    if issued_dt is None or expires_dt is None:
        return ["cosign: issuedAt and expiresAt must be valid ISO-8601 UTC timestamps"]
    if expires_dt <= issued_dt:
        return ["cosign: expiresAt must be after issuedAt"]
    if (expires_dt - issued_dt) > timedelta(minutes=max_ttl_minutes):
        return [f"cosign: TTL exceeds max_ttl_minutes ({max_ttl_minutes})"]

    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if issued_dt > now + timedelta(seconds=60):
        return ["cosign: issuedAt is in the future"]
    if expires_dt <= now:
        return ["cosign: co-signature has expired"]

    keys = channel_keys or {}
    raw_key = keys.get(channel)
    if not raw_key:
        return [f"cosign: no verification key configured for channel {channel!r}"]
    key_bytes = raw_key.encode("utf-8") if isinstance(raw_key, str) else raw_key

    sig = str(cosignature.get("signature") or "").strip().lower()
    if not sig:
        return ["cosign: missing cryptographic signature"]

    expected_msg = canonical_cosign_message(
        plan_id=plan_id,
        full_plan_hash=full_hash,
        signer_oid=signer_oid,
        channel=channel,
        issued_at=issued_raw,
        expires_at=expires_raw,
    )
    expected_sig = hmac.new(key_bytes, expected_msg, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return ["cosign: signature verification failed"]

    return []
