# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Strict R17 redaction for external notification channels (docs/notifications.md section 4)."""

from __future__ import annotations

import urllib.parse
from typing import Any

from .models import NotificationEvent, RedactedNotification

FORBIDDEN_R17_KEYS = frozenset(
    {
        "targets",
        "target",
        "targetscope",
        "hostnames",
        "hostname",
        "resourceids",
        "resourceid",
        "hostpoolnames",
        "hostpoolname",
        "userupns",
        "userupn",
        "parameters",
        "why",
        "conversationhistory",
        "evidence",
        "transcript",
    }
)


def _clean_plan_id(plan_id: str) -> str:
    cleaned = urllib.parse.quote(plan_id.strip() or "unknown", safe="-_.")
    return cleaned


def _clean_github_run_url(raw_url: str) -> str:
    if not raw_url.strip():
        return ""
    parsed = urllib.parse.urlsplit(raw_url.strip())
    if parsed.scheme not in ("https", "http") or not parsed.netloc:
        return ""
    # Strip query string and fragment to prevent parameter leakage
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def build_redacted_notification(
    event: NotificationEvent,
    *,
    urgency: str,
    prefix_length: int = 8,
) -> RedactedNotification:
    """Builds an R17-compliant notification payload carrying only allowed metadata."""
    safe_plan_id = _clean_plan_id(event.plan_id)
    plan_hash_prefix = (event.plan_hash or "").strip()[:prefix_length]
    host = (event.buddy_host or "avd-buddy.example.invalid").strip().rstrip("/")
    if host.startswith("https://") or host.startswith("http://"):
        base_url = host
    else:
        base_url = f"https://{host}"

    buddy_deep_link = f"{base_url}/plans/{safe_plan_id}"
    trip_url = f"{base_url}/plans/{safe_plan_id}#trip"
    github_url = _clean_github_run_url(event.github_run_url)

    return RedactedNotification(
        plan_id=event.plan_id.strip() or "unknown",
        plan_hash_prefix=plan_hash_prefix,
        action=(event.action or event.event_type).strip(),
        urgency=urgency,
        expires_at=event.expires_at.strip(),
        buddy_deep_link=buddy_deep_link,
        github_run_url=github_url,
        trip_url=trip_url,
    )


def assert_r17_compliant(payload: Any) -> None:
    """Recursively verifies that no forbidden R17 field or query parameter appears in a payload."""
    if isinstance(payload, dict):
        for k, v in payload.items():
            if str(k).strip().lower() in FORBIDDEN_R17_KEYS:
                raise ValueError(f"R17 violation: forbidden key {k!r} in external notification payload")
            assert_r17_compliant(v)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            assert_r17_compliant(item)
    elif isinstance(payload, str):
        if "/plans/" in payload and "?" in payload:
            raise ValueError("R17 violation: /plans/<planId> deep link must not contain query parameters")
