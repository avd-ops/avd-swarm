# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""The four v1 notification channels (docs/notifications.md section 4) with strict R17 redaction."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Protocol

from .models import DeliveryResult, RedactedNotification
from .redaction import assert_r17_compliant
from .store import StateStore


class SecretProvider(Protocol):
    def get_secret(self, ref: str) -> str: ...


class InMemorySecretProvider:
    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self._secrets = dict(secrets or {})

    def get_secret(self, ref: str) -> str:
        return self._secrets.get(ref, "")


class KeyVaultSecretProvider:
    """Reads `kv://<secret-name>` references from Azure Key Vault via Managed Identity."""

    def __init__(self, vault_url: str | None = None) -> None:
        self._vault_url = (vault_url or os.environ.get("KEY_VAULT_URL", "")).strip()
        self._client: Any = None

    def get_secret(self, ref: str) -> str:
        if not ref.startswith("kv://"):
            return ""
        name = ref[len("kv://") :]
        if not self._vault_url:
            return os.environ.get(f"KV_FALLBACK_{name.upper().replace('-', '_')}", "")
        if self._client is None:
            from azure.identity import DefaultAzureCredential  # type: ignore[import-not-found]
            from azure.keyvault.secrets import SecretClient  # type: ignore[import-not-found]

            self._client = SecretClient(vault_url=self._vault_url, credential=DefaultAzureCredential())
        return self._client.get_secret(name).value or ""


class HttpTransport(Protocol):
    def post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[int, str]: ...


class UrllibHttpTransport:
    def __init__(self, timeout_seconds: float = 5.0) -> None:
        self._timeout = timeout_seconds

    def post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[int, str]:
        data = json.dumps(payload).encode("utf-8")
        req_headers = {"Content-Type": "application/json; charset=utf-8", **(headers or {})}
        req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return int(resp.status), resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            return int(exc.code), exc.read().decode("utf-8", errors="replace")


def _post_with_failover_status(
    transport: HttpTransport,
    url: str,
    payload: dict[str, Any],
    *,
    channel: str,
    contact_id: str,
    headers: dict[str, str] | None = None,
    cost_usd: float = 0.0,
) -> DeliveryResult:
    assert_r17_compliant(payload)
    if not url:
        return DeliveryResult(channel=channel, contact_id=contact_id, ok=False, status_code=500, error="missing-endpoint")
    try:
        status, _ = transport.post_json(url, payload, headers=headers)
    except Exception as exc:
        return DeliveryResult(channel=channel, contact_id=contact_id, ok=False, status_code=504, error=str(exc))
    if 200 <= status < 300:
        return DeliveryResult(channel=channel, contact_id=contact_id, ok=True, status_code=status, cost_usd=cost_usd)
    return DeliveryResult(
        channel=channel,
        contact_id=contact_id,
        ok=False,
        status_code=status,
        error=f"http-{status}",
    )


class WebPushChannel:
    """avd-buddy Web Push: sends only the fixed payload 'avd-buddy needs you'."""

    FIXED_PAYLOAD = "avd-buddy needs you"

    def __init__(self, transport: HttpTransport, secrets: SecretProvider, vapid_ref: str = "kv://vapid-private-key") -> None:
        self._transport = transport
        self._secrets = secrets
        self._vapid_ref = vapid_ref

    def send(
        self,
        contact_id: str,
        endpoint_ref: str,
        notification: RedactedNotification,
        *,
        buddy_stopped: bool = False,
    ) -> DeliveryResult:
        if buddy_stopped:
            return DeliveryResult(
                channel="web_push",
                contact_id=contact_id,
                ok=False,
                status_code=503,
                error="avd-buddy-stopped",
            )
        _ = self._secrets.get_secret(self._vapid_ref)
        endpoint_url = self._secrets.get_secret(endpoint_ref)
        payload = {"title": self.FIXED_PAYLOAD, "body": self.FIXED_PAYLOAD}
        return _post_with_failover_status(
            self._transport,
            endpoint_url,
            payload,
            channel="web_push",
            contact_id=contact_id,
        )


class TeamsAdaptiveCardChannel:
    """Microsoft Teams Adaptive Card with redacted fields, deep links, POST trip, and optional co-sign."""

    def __init__(self, transport: HttpTransport, secrets: SecretProvider, *, chatops_enabled: bool = False) -> None:
        self._transport = transport
        self._secrets = secrets
        self._chatops_enabled = chatops_enabled

    def build_card(self, notification: RedactedNotification) -> dict[str, Any]:
        facts = [
            {"title": "Plan ID", "value": notification.plan_id},
            {"title": "Plan Hash", "value": notification.plan_hash_prefix or "pending"},
            {"title": "Action", "value": notification.action},
            {"title": "Urgency", "value": notification.urgency},
        ]
        if notification.expires_at:
            facts.append({"title": "Expires", "value": notification.expires_at})

        actions: list[dict[str, Any]] = [
            {"type": "Action.OpenUrl", "title": "Review in avd-buddy", "url": notification.buddy_deep_link},
        ]
        if notification.github_run_url:
            actions.append({"type": "Action.OpenUrl", "title": "Approve in GitHub", "url": notification.github_run_url})
        actions.append(
            {
                "type": "Action.OpenUrl",
                "title": "Trip Kill Switch",
                "url": notification.trip_url,
            }
        )
        actions.append(
            {
                "type": "Action.Submit",
                "title": "Co-sign (Optional)",
                "data": {
                    "kind": "cosign",
                    "planId": notification.plan_id,
                    "planHashPrefix": notification.plan_hash_prefix,
                    "channel": "teams",
                },
            }
        )
        if self._chatops_enabled:
            actions.append(
                {
                    "type": "Action.Submit",
                    "title": "Approve via ChatOps",
                    "data": {
                        "kind": "chatops_approve",
                        "planId": notification.plan_id,
                        "planHashPrefix": notification.plan_hash_prefix,
                        "channel": "teams",
                    },
                }
            )

        card = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.5",
                        "body": [
                            {
                                "type": "TextBlock",
                                "text": f"avd-swarm [{notification.urgency.upper()}]: {notification.action}",
                                "weight": "Bolder",
                                "size": "Medium",
                            },
                            {"type": "FactSet", "facts": facts},
                        ],
                        "actions": actions,
                    },
                }
            ],
        }
        assert_r17_compliant(card)
        return card

    def send(self, contact_id: str, endpoint_ref: str, notification: RedactedNotification) -> DeliveryResult:
        webhook_url = self._secrets.get_secret(endpoint_ref)
        card = self.build_card(notification)
        return _post_with_failover_status(
            self._transport,
            webhook_url,
            card,
            channel="teams",
            contact_id=contact_id,
        )


class EmailChannel:
    """Redacted email via Azure Communication Services or Logic App."""

    def __init__(self, transport: HttpTransport, secrets: SecretProvider) -> None:
        self._transport = transport
        self._secrets = secrets

    def build_payload(self, notification: RedactedNotification) -> dict[str, Any]:
        subject = f"[avd-swarm {notification.urgency.upper()}] {notification.plan_id} ({notification.action})"
        lines = [
            f"Plan ID: {notification.plan_id}",
            f"Plan Hash (8-char): {notification.plan_hash_prefix or 'pending'}",
            f"Action: {notification.action}",
            f"Urgency: {notification.urgency}",
            f"Expires: {notification.expires_at or 'n/a'}",
            f"Review in avd-buddy: {notification.buddy_deep_link}",
        ]
        if notification.github_run_url:
            lines.append(f"Approve in GitHub: {notification.github_run_url}")
        lines.append(f"Trip Kill Switch (Entra POST confirmation): {notification.trip_url}")
        payload = {"subject": subject, "text": "\n".join(lines)}
        assert_r17_compliant(payload)
        return payload

    def send(self, contact_id: str, endpoint_ref: str, notification: RedactedNotification) -> DeliveryResult:
        endpoint_url = self._secrets.get_secret(endpoint_ref)
        payload = self.build_payload(notification)
        return _post_with_failover_status(
            self._transport,
            endpoint_url,
            payload,
            channel="email",
            contact_id=contact_id,
        )


class SmsVoiceChannel:
    """Short redacted SMS/Voice alert (critical or escalated actionable only) with monthly spend cap."""

    def __init__(
        self,
        transport: HttpTransport,
        secrets: SecretProvider,
        store: StateStore,
        *,
        monthly_spend_cap_usd: float = 25.00,
        unit_cost_usd: float = 0.01,
    ) -> None:
        self._transport = transport
        self._secrets = secrets
        self._store = store
        self._monthly_cap = monthly_spend_cap_usd
        self._unit_cost = unit_cost_usd

    def build_payload(self, notification: RedactedNotification) -> dict[str, Any]:
        msg = (
            f"avd-swarm [{notification.urgency.upper()}]: "
            f"{notification.plan_id} ({notification.action}) awaiting action. "
            f"Check avd-buddy or GitHub."
        )
        payload = {"message": msg, "planId": notification.plan_id}
        assert_r17_compliant(payload)
        return payload

    def send(
        self,
        contact_id: str,
        endpoint_ref: str,
        notification: RedactedNotification,
        *,
        month_key: str,
        is_escalated: bool = False,
    ) -> DeliveryResult:
        if notification.urgency != "critical" and not (notification.urgency == "actionable" and is_escalated):
            return DeliveryResult(
                channel="sms_voice",
                contact_id=contact_id,
                ok=False,
                status_code=403,
                error="sms-restricted-to-critical-or-escalated",
            )
        current_spend = self._store.get_monthly_sms_spend(month_key)
        if current_spend + self._unit_cost > self._monthly_cap:
            return DeliveryResult(
                channel="sms_voice",
                contact_id=contact_id,
                ok=False,
                status_code=429,
                error="sms-monthly-spend-cap-exceeded",
            )
        endpoint_url = self._secrets.get_secret(endpoint_ref)
        payload = self.build_payload(notification)
        result = _post_with_failover_status(
            self._transport,
            endpoint_url,
            payload,
            channel="sms_voice",
            contact_id=contact_id,
            cost_usd=self._unit_cost,
        )
        if result.ok:
            self._store.add_monthly_sms_spend(month_key, self._unit_cost)
        return result
