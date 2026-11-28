# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Typed models and monotonic switch helpers for the avd-swarm outside observer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SWITCH_ORDER: dict[str, int] = {
    "run": 0,
    "halt-actions": 1,
    "halt-agents": 2,
    "halt-all": 3,
}
TRIP_POSITIONS: tuple[str, ...] = ("halt-actions", "halt-agents", "halt-all")


def is_monotonic_upgrade(current_position: str, target_position: str) -> bool:
    """Returns True only when target_position is a valid trip position strictly stronger than current_position."""
    if target_position not in TRIP_POSITIONS:
        return False
    cur = SWITCH_ORDER.get(current_position, 0)
    tgt = SWITCH_ORDER[target_position]
    return tgt > cur


def strongest_position(positions: list[str]) -> str | None:
    """Returns the strongest trip position in `positions`, or None if empty."""
    valid = [p for p in positions if p in TRIP_POSITIONS]
    if not valid:
        return None
    return max(valid, key=lambda p: SWITCH_ORDER[p])


@dataclass(frozen=True)
class ActivityWrite:
    """Normalized Azure Activity Log write event."""

    event_id: str
    caller_id: str
    caller_role: str  # e.g. approve, stop-spend, sre-agent, hosts, cost, intune, recovery-test, unknown
    resource_id: str
    operation_name: str
    timestamp: str
    status: str = "Succeeded"


@dataclass(frozen=True)
class GraphAuditEvent:
    """Normalized Microsoft Entra audit or sign-in event from Graph GET."""

    event_id: str
    actor_id: str
    activity: str
    target_id: str
    timestamp: str
    category: str = "DirectoryAudit"


@dataclass(frozen=True)
class ApprovedPlanEvent:
    """Signed `plan.approved` CloudEvent read from the immutable blob audit store."""

    plan_id: str
    plan_hash: str
    action: str
    targets: tuple[str, ...]
    approved_at: str
    expires_at: str
    approver: str = ""
    signature_valid: bool = True


@dataclass(frozen=True)
class HeartbeatRecord:
    """Latest avd-swarm heartbeat timestamp and switch state."""

    timestamp: str
    switch_position: str = "run"
    component: str = "avd-swarm"


@dataclass(frozen=True)
class TokenMeterState:
    """Token meter snapshot used to verify halt-agents compliance."""

    daily_spend_usd: float
    daily_cap_usd: float
    last_model_call_at: str = ""
    halted_at: str = ""


@dataclass
class CorroborationFinding:
    """Single deterministic rule violation detected during corroboration."""

    rule_id: str
    position: str
    reason: str
    page_owner: bool = False
    evidence_id: str = ""


@dataclass
class CorroborationResult:
    """Outcome of a 5-minute Layer 1 corroboration run."""

    healthy: bool
    tripped: bool
    trip_position: str | None = None
    findings: list[CorroborationFinding] = field(default_factory=list)
    deadman_pinged: bool = False
    checked_writes: int = 0
    checked_graph_events: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "tripped": self.tripped,
            "tripPosition": self.trip_position,
            "findings": [
                {
                    "ruleId": f.rule_id,
                    "position": f.position,
                    "reason": f.reason,
                    "pageOwner": f.page_owner,
                    "evidenceId": f.evidence_id,
                }
                for f in self.findings
            ],
            "deadmanPinged": self.deadman_pinged,
            "checkedWrites": self.checked_writes,
            "checkedGraphEvents": self.checked_graph_events,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class ApprovalTelemetry:
    """Recent C10 approval record for approval-fatigue streak detection."""

    plan_id: str
    decision: str  # "approved" | "rejected"
    latency_seconds: float
    approver: str = ""


@dataclass
class VerdictResult:
    """Fail-closed verdict response for a `plan.proposed` CloudEvent."""

    plan_id: str
    verdict: str  # "allow" | "deny"
    reasons: list[str] = field(default_factory=list)
    deterministic_passed: bool = False
    fatigue_detected: bool = False
    model_used: bool = False
    trip_recommended: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "planId": self.plan_id,
            "verdict": self.verdict,
            "reasons": list(self.reasons),
            "deterministicPassed": self.deterministic_passed,
            "fatigueDetected": self.fatigue_detected,
            "modelUsed": self.model_used,
            "tripRecommended": self.trip_recommended,
        }
