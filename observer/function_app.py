# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Azure Functions Consumption entrypoint for the 3-layer avd-swarm outside observer."""

from __future__ import annotations

import json
import os
from typing import Any, Callable

from .auth import authenticate_request
from .corroborator import Corroborator, config_from_env
from .models import (
    ActivityWrite,
    ApprovalTelemetry,
    ApprovedPlanEvent,
    CorroborationResult,
    GraphAuditEvent,
    HeartbeatRecord,
    TokenMeterState,
)
from .verdict import evaluate_verdict


def handle_verdict_http(
    headers: dict[str, str],
    raw_body: bytes,
    *,
    hmac_secret: str | None = None,
    cloud_env: str | None = None,
    recent_approvals: list[ApprovalTelemetry] | None = None,
    model_reviewer: Callable[[dict[str, Any], int], tuple[bool, str]] | None = None,
    trip_callback: Callable[[str, str], None] | None = None,
    now_ts: float | None = None,
) -> tuple[int, dict[str, Any]]:
    """Stateless HTTP `verdict` handler (usable in Azure Functions and offline tests). Fail-closed."""
    secret = hmac_secret if hmac_secret is not None else os.environ.get("OBSERVER_HMAC_SECRET", "")
    ok, auth_reason = authenticate_request(headers, raw_body, hmac_secret=secret, require_auth=True, now_ts=now_ts)
    if not ok:
        return 401, {"verdict": "deny", "reasons": [auth_reason]}

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        return 400, {"verdict": "deny", "reasons": ["invalid JSON body"]}

    if not isinstance(payload, dict):
        return 400, {"verdict": "deny", "reasons": ["JSON body must be an object"]}

    env_cloud = cloud_env or os.environ.get("CLOUD_ENVIRONMENT", "commercial").strip() or "commercial"
    result = evaluate_verdict(
        payload,
        cloud_env=env_cloud,
        recent_approvals=recent_approvals,
        model_reviewer=model_reviewer,
        max_tokens=int(os.environ.get("OBSERVER_MAX_TOKENS", "256")),
        trip_callback=trip_callback,
    )
    status = 200 if result.verdict == "allow" else 403
    return status, result.to_dict()


def run_corroboration_timer(
    *,
    activity_writes: list[ActivityWrite] | None = None,
    graph_events: list[GraphAuditEvent] | None = None,
    approved_plans: list[ApprovedPlanEvent] | None = None,
    heartbeat: HeartbeatRecord | None = None,
    token_meter: TokenMeterState | None = None,
) -> CorroborationResult:
    """Runs one 5-minute corroboration pass using environment configuration."""
    corroborator = Corroborator(config=config_from_env())
    return corroborator.run_once(
        activity_writes=activity_writes or [],
        graph_events=graph_events or [],
        approved_plans=approved_plans or [],
        heartbeat=heartbeat,
        token_meter=token_meter,
    )


try:
    import azure.functions as func  # type: ignore[import-not-found]

    app = func.FunctionApp()

    @app.timer_trigger(schedule="0 */5 * * * *", arg_name="timer", run_on_startup=False)
    def corroborator_timer(timer: func.TimerRequest) -> None:
        _ = timer
        run_corroboration_timer()

    @app.route(route="verdict", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
    def verdict_webhook(req: func.HttpRequest) -> func.HttpResponse:
        status, body = handle_verdict_http(dict(req.headers), req.get_body())
        return func.HttpResponse(
            body=json.dumps(body),
            status_code=status,
            mimetype="application/json",
        )
except ImportError:
    app = None  # Offline test environment without azure-functions installed
