# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Phase 2 Direct ChatOps Approval Proxy (docs/notifications.md section 5; decision 0011).

Enforces per-user Entra ID + per-user GitHub OAuth identity binding (never a shared bot
installation token), verifies the kill switch and full 64-char SHA-256 planHash, and calls
GitHub's Deployment Review API (`POST /repos/{owner}/{repo}/actions/runs/{run_id}/pending_deployments`).
"""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from typing import Any, Callable

from .channels import HttpTransport
from .redaction import assert_r17_compliant

_FULL_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class ChatOpsApprovalRequest:
    platform: str
    entra_oid: str
    github_user_token: str
    github_actor: str
    plan_id: str
    full_plan_hash: str
    owner: str
    repo: str
    run_id: int
    environment_id: int
    comment: str = ""
    stop_spend: bool = False
    token_type: str = "user_oauth"
    is_shared_bot_token: bool = False


class ChatOpsApprovalProxy:
    """Validates ChatOps approvals and submits them as the authenticated human user."""

    def __init__(
        self,
        *,
        transport: HttpTransport,
        read_switch_position: Callable[[], str],
        read_current_plan_hash: Callable[[str], str],
        allowed_platforms: tuple[str, ...] = ("teams", "slack"),
        github_api_base: str = "https://api.github.com",
    ) -> None:
        self._transport = transport
        self._read_switch_position = read_switch_position
        self._read_current_plan_hash = read_current_plan_hash
        self._allowed_platforms = {p.lower() for p in allowed_platforms}
        self._api_base = github_api_base.rstrip("/")

    def _preflight_errors(self, req: ChatOpsApprovalRequest) -> list[str]:
        errors: list[str] = []

        if req.platform.strip().lower() not in self._allowed_platforms:
            errors.append(f"chatops: platform {req.platform!r} is not allowed")

        if not req.entra_oid.strip():
            errors.append("chatops: missing Entra ID user identity (entra_oid)")

        token = req.github_user_token.strip()
        if not token:
            errors.append("chatops: missing per-user GitHub OAuth token")
        elif (
            req.is_shared_bot_token
            or req.token_type.strip().lower() in ("installation", "app", "shared_bot")
            or token.startswith("ghs_")
        ):
            errors.append("chatops: shared bot or GitHub App installation tokens are forbidden; per-user OAuth required")

        actor = req.github_actor.strip()
        if not actor or actor.lower().endswith("[bot]"):
            errors.append("chatops: github_actor must be a human user, not a bot account")

        full_hash = req.full_plan_hash.strip().lower()
        if not _FULL_SHA256_RE.match(full_hash):
            errors.append("chatops: fullPlanHash must be a full 64-char SHA-256 hex digest (8-char prefix rejected)")
        else:
            current_hash = self._read_current_plan_hash(req.plan_id).strip().lower()
            if not _FULL_SHA256_RE.match(current_hash) or not hmac.compare_digest(full_hash, current_hash):
                errors.append("chatops: fullPlanHash does not match current plan hash (drift detected)")

        switch_pos = self._read_switch_position().strip()
        if switch_pos != "run" and not req.stop_spend:
            errors.append(f"chatops: kill switch position {switch_pos!r} refuses standard approvals")

        if not (_SAFE_SLUG_RE.match(req.owner) and _SAFE_SLUG_RE.match(req.repo)):
            errors.append("chatops: invalid GitHub owner or repo")
        if int(req.run_id) <= 0 or int(req.environment_id) <= 0:
            errors.append("chatops: run_id and environment_id must be positive integers")

        return errors

    def submit_decision(self, req: ChatOpsApprovalRequest, *, state: str = "approved") -> dict[str, Any]:
        if state not in ("approved", "rejected"):
            return {"status": "refused", "reasons": [f"chatops: invalid decision state {state!r}"]}

        errors = self._preflight_errors(req)
        if errors:
            return {"status": "refused", "planId": req.plan_id, "reasons": errors}

        url = f"{self._api_base}/repos/{req.owner}/{req.repo}/actions/runs/{int(req.run_id)}/pending_deployments"
        comment = req.comment.strip() or f"ChatOps {state} via {req.platform} (plan {req.plan_id})"
        payload = {
            "environment_ids": [int(req.environment_id)],
            "state": state,
            "comment": comment,
        }
        assert_r17_compliant(payload)

        headers = {
            "Authorization": f"Bearer {req.github_user_token.strip()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            http_status, _ = self._transport.post_json(url, payload, headers=headers)
        except Exception as exc:
            return {"status": "error", "planId": req.plan_id, "reasons": [f"chatops: GitHub API error: {exc}"]}

        if 200 <= http_status < 300:
            return {
                "status": state,
                "planId": req.plan_id,
                "planHashPrefix": req.full_plan_hash[:8],
                "approvedBy": req.github_actor,
                "platform": req.platform,
                "httpStatus": http_status,
            }
        return {
            "status": "refused",
            "planId": req.plan_id,
            "httpStatus": http_status,
            "reasons": [f"chatops: GitHub Deployment Review API returned HTTP {http_status}"],
        }

    def approve(self, req: ChatOpsApprovalRequest) -> dict[str, Any]:
        return self.submit_decision(req, state="approved")

    def reject(self, req: ChatOpsApprovalRequest) -> dict[str, Any]:
        return self.submit_decision(req, state="rejected")
