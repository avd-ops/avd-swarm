# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Data models for the Notification & Escalation Router."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NotificationEvent:
    event_type: str
    plan_id: str = ""
    plan_hash: str = ""
    action: str = ""
    agent_id: str = ""
    switch_position: str = ""
    urgent: bool = False
    stop_spend: bool = False
    expires_at: str = ""
    buddy_host: str = "avd-buddy.example.invalid"
    github_run_url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RedactedNotification:
    plan_id: str
    plan_hash_prefix: str
    action: str
    urgency: str
    expires_at: str
    buddy_deep_link: str
    github_run_url: str
    trip_url: str

    def to_dict(self) -> dict[str, str]:
        return {
            "planId": self.plan_id,
            "planHashPrefix": self.plan_hash_prefix,
            "action": self.action,
            "urgency": self.urgency,
            "expiresAt": self.expires_at,
            "buddyDeepLink": self.buddy_deep_link,
            "githubRunUrl": self.github_run_url,
            "tripUrl": self.trip_url,
        }


@dataclass(frozen=True)
class DeliveryResult:
    channel: str
    contact_id: str
    ok: bool
    status_code: int = 200
    error: str = ""
    cost_usd: float = 0.0


@dataclass
class IncidentState:
    incident_key: str
    event_type: str
    plan_id: str
    urgency: str
    status: str  # open, acknowledged, cancelled, escalated, completed
    current_step: int
    next_escalation_at: str | None
    contact_tier: str  # primary, fallback
    payload: dict[str, str]
    updated_at: str
