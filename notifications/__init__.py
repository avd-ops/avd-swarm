# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Smart Notification, Escalation & Multi-Platform Approval Router (docs/notifications.md; decision 0011)."""

from .chatops import ChatOpsApprovalProxy, ChatOpsApprovalRequest
from .models import DeliveryResult, IncidentState, NotificationEvent, RedactedNotification
from .redaction import assert_r17_compliant, build_redacted_notification
from .router import NotificationRouter, load_notifications_policy
from .store import InMemoryStateStore, StateStore, TableStorageStateStore

__all__ = [
    "ChatOpsApprovalProxy",
    "ChatOpsApprovalRequest",
    "DeliveryResult",
    "InMemoryStateStore",
    "IncidentState",
    "NotificationEvent",
    "NotificationRouter",
    "RedactedNotification",
    "StateStore",
    "TableStorageStateStore",
    "assert_r17_compliant",
    "build_redacted_notification",
    "load_notifications_policy",
]
