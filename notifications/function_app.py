# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Azure Functions Consumption entrypoint for the Notification & Escalation Router."""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Callable

from .channels import HttpTransport, KeyVaultSecretProvider, UrllibHttpTransport
from .chatops import ChatOpsApprovalProxy, ChatOpsApprovalRequest
from .models import NotificationEvent
from .router import NotificationRouter, load_notifications_policy
from .store import InMemoryStateStore, TableStorageStateStore


def _build_router() -> NotificationRouter:
    policy = load_notifications_policy()
    if os.environ.get("AZURE_TABLES_ENDPOINT", "").strip():
        store = TableStorageStateStore()
    else:
        store = InMemoryStateStore()
    return NotificationRouter(
        policy=policy,
        store=store,
        transport=UrllibHttpTransport(),
        secrets=KeyVaultSecretProvider(),
    )


def handle_event_grid_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Processes a single Event Grid event dict (usable both in Azure Functions and offline tests)."""
    router = _build_router()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    event_type = str(payload.get("eventType") or data.get("eventType") or "router.heartbeat")

    if event_type == "Microsoft.EventGrid.DeadLetter":
        return router.handle_dead_letter(data)

    event = NotificationEvent(
        event_type=event_type,
        plan_id=str(data.get("planId") or ""),
        plan_hash=str(data.get("planHash") or ""),
        action=str(data.get("action") or ""),
        agent_id=str(data.get("agentId") or ""),
        switch_position=str(data.get("switchPosition") or ""),
        urgent=bool(data.get("urgent", False)),
        stop_spend=bool(data.get("stopSpend", False)),
        expires_at=str(data.get("expiresAt") or ""),
        buddy_host=str(data.get("buddyHost") or os.environ.get("AVD_BUDDY_HOST", "avd-buddy.example.invalid")),
        github_run_url=str(data.get("githubRunUrl") or ""),
    )
    switch_pos = str(data.get("currentSwitchPosition") or os.environ.get("SWITCH_POSITION", "run"))
    return router.route_event(event, switch_position=switch_pos)


def _extract_entra_oid(headers: dict[str, str]) -> str:
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


def handle_chatops_http(
    headers: dict[str, str],
    body: dict[str, Any],
    *,
    read_switch_position: Callable[[], str],
    read_current_plan_hash: Callable[[str], str],
    transport: HttpTransport | None = None,
) -> tuple[int, dict[str, Any]]:
    """Handles an authenticated ChatOps approval/rejection HTTP request."""
    entra_oid = _extract_entra_oid(headers)
    if not entra_oid:
        return 401, {"status": "refused", "reasons": ["chatops: missing Entra Easy Auth principal"]}

    lowered = {k.lower(): v for k, v in headers.items()}
    github_token = lowered.get("x-github-user-token", "").strip()
    github_actor = lowered.get("x-github-user-login", "").strip() or str(body.get("githubActor") or "").strip()

    proxy = ChatOpsApprovalProxy(
        transport=transport or UrllibHttpTransport(),
        read_switch_position=read_switch_position,
        read_current_plan_hash=read_current_plan_hash,
    )
    req = ChatOpsApprovalRequest(
        platform=str(body.get("platform") or "teams"),
        entra_oid=entra_oid,
        github_user_token=github_token,
        github_actor=github_actor,
        plan_id=str(body.get("planId") or ""),
        full_plan_hash=str(body.get("fullPlanHash") or ""),
        owner=str(body.get("owner") or ""),
        repo=str(body.get("repo") or ""),
        run_id=int(body.get("runId") or 0),
        environment_id=int(body.get("environmentId") or 0),
        comment=str(body.get("comment") or ""),
        stop_spend=bool(body.get("stopSpend", False)),
    )
    decision = str(body.get("decision") or "approved").strip().lower()
    result = proxy.submit_decision(req, state=decision)
    status_code = 200 if result.get("status") in ("approved", "rejected") else 400
    return status_code, result


try:
    import azure.functions as func  # type: ignore[import-not-found]

    app = func.FunctionApp()

    @app.event_grid_trigger(arg_name="event")
    def on_swarm_event(event: func.EventGridEvent) -> None:
        handle_event_grid_payload({"eventType": event.event_type, "data": event.get_json()})

    @app.timer_trigger(schedule="0 */1 * * * *", arg_name="timer", run_on_startup=False)
    def on_escalation_timer(timer: func.TimerRequest) -> None:
        _ = timer
        router = _build_router()
        router.process_escalations()
        router.emit_heartbeat()
except ImportError:
    app = None  # Offline test environment without azure-functions installed
    _ = json
