# avd-swarm runner: redacted ticket closeout from approved execution records. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Post redacted C10 / apply_approved_plan outcomes back to an external ticket adapter.

Follows avd-ops ADR 0027 (5-method adapter boundary) and red-team rule R17:
never include host names, resource IDs, user emails, or parameters in ticket notes.
"""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Any
from urllib import parse, request

TICKET_PROVIDERS = ("mock", "jira-jsm", "servicenow", "github")
TICKET_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

DEFAULT_JIRA_STATUS_MAP = {
    "new": "Waiting for support",
    "triaging": "In Progress",
    "awaiting_approval": "Waiting for approval",
    "resolved": "Resolved",
}


def normalize_ticket_ref(ref: dict[str, Any]) -> dict[str, str]:
    """Validate and normalize an avd-ticket-ref/v1 envelope."""
    if not isinstance(ref, dict):
        raise ValueError("ticketRef must be a dict")
    provider = str(ref.get("provider", "")).lower()
    ticket_id = str(ref.get("ticketId", ""))
    if provider not in TICKET_PROVIDERS:
        raise ValueError(f"Unsupported ticketRef.provider: {provider}")
    if not TICKET_ID_RE.fullmatch(ticket_id):
        raise ValueError("Invalid ticketRef.ticketId")
    out: dict[str, str] = {
        "schema": "avd-ticket-ref/v1",
        "provider": provider,
        "ticketId": ticket_id,
    }
    corr = ref.get("correlationId")
    if corr is not None and corr != "":
        corr_str = str(corr)
        if not TICKET_ID_RE.fullmatch(corr_str):
            raise ValueError("Invalid ticketRef.correlationId")
        out["correlationId"] = corr_str
    return out


def format_closeout_note(execution_record: dict[str, Any], ticket_ref: dict[str, str]) -> str:
    """Format a strictly redacted execution summary (R17: counts, action, and planHash only)."""
    ref = normalize_ticket_ref(ticket_ref)
    status = str(execution_record.get("status", "unknown"))
    lines = [
        "[avd-swarm execution outcome]",
        f"Ticket: {ref['ticketId']} ({ref['provider']})",
        f"Plan: {execution_record.get('planId', 'unknown')} | Hash: {execution_record.get('planHash', 'unknown')}",
        f"Action: {execution_record.get('action', 'unknown')} | Status: {status}",
        f"Verification: passed={int(execution_record.get('passedCount', 0))}, failed={int(execution_record.get('failedCount', 0))}",
    ]
    if "correlationId" in ref:
        lines.append(f"Correlation: {ref['correlationId']}")
    return "\n".join(lines)


class MockTicketAdapter:
    """In-memory ticket adapter for $0 offline verification."""

    provider = "mock"

    def __init__(self, initial_tickets: list[dict[str, Any]] | None = None) -> None:
        self._tickets: dict[str, dict[str, Any]] = {}
        for t in initial_tickets or []:
            self._tickets[t["id"]] = dict(t, comments=list(t.get("comments", [])))

    def get_ticket(self, ticket_id: str) -> dict[str, Any]:
        if ticket_id not in self._tickets:
            raise KeyError(f"Ticket not found: {ticket_id}")
        return dict(self._tickets[ticket_id])

    def add_work_note(self, ticket_id: str, body: str, *, internal: bool = True) -> dict[str, Any]:
        t = self._tickets[ticket_id]
        note = {"id": f"note-{len(t['comments']) + 1}", "internal": bool(internal), "body": body}
        t["comments"].append(note)
        return note

    def transition_ticket(self, ticket_id: str, target_state: str) -> dict[str, Any]:
        t = self._tickets[ticket_id]
        t["status"] = target_state
        return dict(t)


class JiraJsmTicketAdapter:
    """Jira Service Management (Cloud Free) REST v3 closeout adapter."""

    provider = "jira-jsm"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        email: str | None = None,
        api_token: str | None = None,
        status_map: dict[str, str] | None = None,
        opener: Any = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("JIRA_BASE_URL", "")).rstrip("/")
        self.email = email or os.environ.get("JIRA_USER_EMAIL", "")
        self.api_token = api_token or os.environ.get("JIRA_API_TOKEN", "")
        if not (self.base_url and self.email and self.api_token):
            raise ValueError("JiraJsmTicketAdapter requires JIRA_BASE_URL, JIRA_USER_EMAIL, and JIRA_API_TOKEN")
        self.status_map = {**DEFAULT_JIRA_STATUS_MAP, **(status_map or {})}
        self._opener = opener or request.urlopen
        creds = base64.b64encode(f"{self.email}:{self.api_token}".encode("utf-8")).decode("ascii")
        self._auth = f"Basic {creds}"

    def _req(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Authorization": self._auth, "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        with self._opener(req) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None

    def add_work_note(self, ticket_id: str, body: str, *, internal: bool = True) -> Any:
        payload = {
            "body": {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": body}]}]},
            "properties": [{"key": "sd.public.comment", "value": {"internal": bool(internal)}}],
        }
        return self._req("POST", f"/rest/api/3/issue/{parse.quote(ticket_id)}/comment", payload)

    def transition_ticket(self, ticket_id: str, target_state: str) -> dict[str, str]:
        want = self.status_map.get(target_state, target_state).lower()
        data = self._req("GET", f"/rest/api/3/issue/{parse.quote(ticket_id)}/transitions")
        match = next(
            (t for t in data.get("transitions", []) if t.get("name", "").lower() == want or t.get("to", {}).get("name", "").lower() == want),
            None,
        )
        if not match:
            raise ValueError(f"No Jira transition '{want}' on {ticket_id}")
        self._req("POST", f"/rest/api/3/issue/{parse.quote(ticket_id)}/transitions", {"transition": {"id": match["id"]}})
        return {"id": ticket_id, "status": target_state}


def create_ticket_adapter(provider: str, **kwargs: Any) -> Any:
    p = provider.lower()
    if p == "mock":
        return MockTicketAdapter(**kwargs)
    if p == "jira-jsm":
        return JiraJsmTicketAdapter(**kwargs)
    if p == "servicenow":
        raise NotImplementedError("Provider 'servicenow' is reserved for avd-servicenow.")
    raise ValueError(f"Unsupported ticket provider: {provider}")


def closeout_ticket(
    execution_record: dict[str, Any],
    ticket_ref: dict[str, str],
    adapter: Any = None,
) -> dict[str, Any]:
    """Post redacted execution outcome to the ticket and transition to resolved or triaging."""
    ref = normalize_ticket_ref(ticket_ref)
    client = adapter or create_ticket_adapter(ref["provider"])
    note = format_closeout_note(execution_record, ref)
    succeeded = execution_record.get("status") == "succeeded"
    next_state = "resolved" if succeeded else "triaging"

    client.add_work_note(ref["ticketId"], note, internal=not succeeded)
    client.transition_ticket(ref["ticketId"], next_state)
    return {"ticketId": ref["ticketId"], "status": next_state, "note": note}
