#!/usr/bin/env python3
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""avd-buddy Container App server (docs/buddy-app.md, docs/decisions/0010-avd-buddy-container-app-hosting.md).

Serves:
- The static PWA shell from buddy/app/ with strict CSP, Referrer-Policy and Permissions-Policy headers on every response.
- Deterministic zero-token status (`GET /api/status`) and server-side pseudonymized history (`GET /api/history`).
- Short-poll conversation turns (`POST /api/turns`, `GET /api/turns/{id}?after=n`), enforcing `scripts/voice_check.py`
  against `governance/buddy-voice.yml` before any reply or aside reaches the client.
- Kill switch trip (`POST /api/switch/trip`), requiring exact confirmation of the halt position and refusing any reset.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import build_buddy_icons  # noqa: E402
import voice_check  # noqa: E402

APP_DIR = ROOT / "buddy/app"
VOICE_SETTINGS_PATH = ROOT / "governance/buddy-voice.yml"

CSP_HEADER = (
    "default-src 'self'; "
    "connect-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self'; "
    "script-src 'self'; "
    "font-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'self'"
)
REFERRER_POLICY = "no-referrer"
PERMISSIONS_POLICY = "camera=(), microphone=(self), geolocation=()"

VALID_HALT_POSITIONS = ("halt-actions", "halt-agents", "halt-all")
SWITCH_MEANINGS = {
    "run": "Normal operation. Agents may read and propose plans; approved plans may run.",
    "halt-actions": "Write path halted. Agents may still answer read-only questions, but no plan can be approved or executed.",
    "halt-agents": "Agents and MCP tool layer halted. Chat composer is off; use the Cloud Shell blocks for read-only inspection or reset.",
    "halt-all": "avd-buddy's API and the tool layer's Container App are stopped.",
}

MIME_OVERRIDES = {
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}

DEFAULT_FIXTURE_PLAN = {
    "planId": "plan-20261108-01",
    "planHash": "a94f3c81e20b9d74c158a02e88b0194f",
    "action": "hosts.drain-and-restart-agent",
    "targets": ["sh-pooled-03 (hp-contoso-prod)"],
    "expectedEffect": "New sessions stop routing to sh-pooled-03; 0 existing sessions affected; RDAgentBootLoader service restarts.",
    "blastRadius": "1 of 6 session hosts in hp-contoso-prod (0 active user sessions)",
    "why": "sh-pooled-03 missed 4 consecutive heartbeats in WVDAgentHealthStatus over the last 15 minutes while the VM power state is running and 0 sessions are active.",
    "gateResults": [
        {"gate": "Allowlist", "outcome": "pass", "text": "Pass: catalog action hosts.drain-and-restart-agent"},
        {
            "gate": "Says why",
            "outcome": "pass",
            "text": "Pass: cites WVDAgentHealthStatus heartbeat gap and Fact Register ID",
            "devText": "Pass: cites WVDAgentHealthStatus heartbeat gap and Fact Register (C15) ID",
        },
        {"gate": "Live re-read", "outcome": "pass", "text": "Pass: 0 active sessions verified before proposal"},
        {"gate": "Kill switch", "outcome": "pass", "text": "Pass: switch position is run"},
    ],
    "rollback": "az desktopvirtualization sessionhost update -g rg-contoso-prod-avd --host-pool-name hp-contoso-prod -n sh-pooled-03 --allow-new-session true",
    "expiry": "2026-11-08T21:30:00Z (expires in 30m)",
    "approveUrl": "https://github.com/avd-ops/avd-swarm/actions",
}


def parse_client_principal(headers: dict[str, str]) -> dict[str, str] | None:
    """Extracts the verified operator identity from Container Apps Easy Auth headers (docs/buddy-app.md section 4)."""
    lowered = {k.lower(): v for k, v in headers.items()}
    encoded = lowered.get("x-ms-client-principal", "").strip()
    if encoded:
        try:
            raw = base64.b64decode(encoded).decode("utf-8")
            payload = json.loads(raw)
            claims = {c.get("typ"): c.get("val") for c in payload.get("claims", []) if isinstance(c, dict)}
            oid = (
                claims.get("http://schemas.microsoft.com/identity/claims/objectidentifier")
                or claims.get("oid")
                or lowered.get("x-ms-client-principal-id", "")
            )
            name = (
                claims.get("preferred_username")
                or claims.get("name")
                or lowered.get("x-ms-client-principal-name", "operator@contoso.com")
            )
            return {"oid": str(oid or "operator"), "name": str(name)}
        except Exception:
            return None
    principal_id = lowered.get("x-ms-client-principal-id", "").strip()
    principal_name = lowered.get("x-ms-client-principal-name", "").strip()
    if principal_id or principal_name:
        return {"oid": principal_id or "operator", "name": principal_name or "operator@contoso.com"}
    return None


def fetch_managed_identity_token(resource: str, client_id: str = "") -> str:
    """Fetches a Bearer token from the Container Apps managed identity endpoint when running in Azure."""
    endpoint = os.environ.get("IDENTITY_ENDPOINT", "").strip()
    header = os.environ.get("IDENTITY_HEADER", "").strip()
    if not endpoint or not header or not resource:
        return ""
    params = {"api-version": "2019-08-01", "resource": resource}
    if client_id:
        params["client_id"] = client_id
    url = f"{endpoint}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"X-IDENTITY-HEADER": header}, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return str(data.get("access_token", ""))


def load_static_files(app_dir: Path = APP_DIR) -> dict[str, tuple[str, bytes]]:
    """Pre-indexes static files under buddy/app/ so request paths never touch the filesystem or mimetypes."""
    table: dict[str, tuple[str, bytes]] = {}
    root = app_dir.resolve()
    for file_path in sorted(root.rglob("*")):
        if not file_path.is_file():
            continue
        rel = "/" + file_path.relative_to(root).as_posix()
        ext = file_path.suffix.lower()
        content_type = MIME_OVERRIDES.get(ext, "application/octet-stream")
        table[rel] = (content_type, file_path.read_bytes())
    if "/index.html" in table:
        table["/"] = table["/index.html"]
    return table


class BuddyState:
    """In-memory state for the avd-buddy server process."""

    def __init__(
        self,
        *,
        require_easy_auth: bool = False,
        sim_mode: bool = True,
        switch_position: str = "run",
        token_spend_usd: float = 1.42,
        token_cap_usd: float = 15.0,
        agent_api_url: str = "",
        agent_model: str = "claude-sonnet-5-5",
        agent_identity_app_id: str = "",
        mcp_app_id: str = "",
        buddy_uami_client_id: str = "",
        token_provider: Callable[[], str] | None = None,
    ) -> None:
        self.require_easy_auth = require_easy_auth
        self.sim_mode = sim_mode
        self.switch_position = switch_position
        self.token_spend_usd = token_spend_usd
        self.token_cap_usd = token_cap_usd
        self.agent_api_url = agent_api_url.rstrip("/")
        self.agent_model = agent_model
        self.agent_identity_app_id = agent_identity_app_id
        self.mcp_app_id = mcp_app_id
        self.buddy_uami_client_id = buddy_uami_client_id
        self.token_provider = token_provider
        self.voice_settings = yaml.safe_load(VOICE_SETTINGS_PATH.read_text(encoding="utf-8"))
        self.static_files = load_static_files(APP_DIR)
        self.turns: dict[str, dict[str, Any]] = {}
        self.replies_since_aside = 99
        self.history: list[dict[str, str]] = [
            {"id": "thread-idle-hosts", "title": "Weekend idle hosts in Sales pool", "updatedAt": "Today, 09:14", "presetKey": "idle-hosts"},
            {"id": "thread-drain-host-03", "title": "Drain sh-pooled-03 and restart agent", "updatedAt": "Today, 08:42", "presetKey": "drain-host"},
            {"id": "thread-deploy-failed", "title": "Subnet overlap on first deployment", "updatedAt": "Yesterday", "presetKey": "deploy-failed"},
            {"id": "thread-max-sessions", "title": "Why maxSessionLimit is 8", "updatedAt": "2 days ago", "presetKey": "max-sessions"},
        ]
        self.estate_status: dict[str, Any] = {
            "estate": "estate-a1f9c204",
            "collectedAt": "2026-11-09T16:20:00Z",
            "overallStatus": "pass",
            "overallScorePct": 94,
            "planes": [
                {"id": "azure", "name": "Azure (avd-azure)", "status": "pass", "scorePct": 96, "pass": 27, "warn": 1, "fail": 0},
                {"id": "entra", "name": "Entra ID (avd-entra)", "status": "pass", "scorePct": 95, "pass": 18, "warn": 1, "fail": 0},
                {"id": "intune", "name": "Intune (avd-intune)", "status": "pass", "scorePct": 92, "pass": 14, "warn": 1, "fail": 0},
                {"id": "m365", "name": "Microsoft 365 (avd-m365)", "status": "pass", "scorePct": 94, "pass": 11, "warn": 1, "fail": 0},
                {"id": "power-platform", "name": "Power Platform (avd-power-platform)", "status": "pass", "scorePct": 91, "pass": 8, "warn": 1, "fail": 0},
            ],
            "hostPools": [
                {
                    "id": "hp-pooled-01",
                    "type": "Pooled",
                    "hostsTotal": 4,
                    "hostsAvailable": 4,
                    "activeSessions": 18,
                    "capacityPct": 56,
                    "autoscale": "enabled",
                }
            ],
            "meterAudit": {
                "runningSessionHosts": 4,
                "deallocatedSessionHosts": 0,
                "unattachedDisks": 0,
                "orphanedNics": 0,
                "publicIps": 0,
                "idleWarnings": [],
            },
            "topFindings": [
                {
                    "checkId": "asm-files-zone-redundant",
                    "plane": "azure",
                    "outcome": "warn",
                    "severity": "medium",
                    "title": "Profile storage account uses LRS instead of ZRS",
                    "askPrompt": "Why is asm-files-zone-redundant warning on our profile storage account and how do we remediate it?",
                }
            ],
        }

    def _get_agent_token(self) -> str:
        if self.token_provider is not None:
            return self.token_provider()
        if self.mcp_app_id:
            try:
                return fetch_managed_identity_token(f"api://{self.mcp_app_id}", self.buddy_uami_client_id)
            except Exception:
                return ""
        return ""

    def _agent_api_request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        operator_claim: dict[str, str] | None = None,
        timeout: float = 5.0,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self.agent_api_url}{path}"
        headers = {"Accept": "application/json"}
        token = self._get_agent_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if operator_claim:
            headers["X-Avd-Operator-Oid"] = operator_claim.get("oid", "")
            headers["X-Avd-Operator-Name"] = operator_claim.get("name", "")
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        parsed = urllib.parse.urlsplit(url)
        if parsed.hostname in ("127.0.0.1", "localhost", "::1"):
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        else:
            opener = urllib.request.build_opener()
        try:
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as err:
            raw = err.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"error": raw}
            return err.code, payload

    def sync_status_from_agent_api(self, operator_claim: dict[str, str]) -> None:
        if self.sim_mode or not self.agent_api_url:
            return
        try:
            status, payload = self._agent_api_request("/healthz", method="GET", operator_claim=operator_claim, timeout=2.5)
            if status == 200 and isinstance(payload.get("meter"), dict):
                meter = payload["meter"]
                if "spentUsd" in meter:
                    self.token_spend_usd = float(meter["spentUsd"])
                if "capUsd" in meter:
                    self.token_cap_usd = float(meter["capUsd"])
                if meter.get("stopped") and self.switch_position == "run":
                    self.switch_position = "halt-agents"
        except Exception:
            pass

    def trip_agent_api_stop(self, position: str, operator_claim: dict[str, str]) -> None:
        if self.sim_mode or not self.agent_api_url or position not in ("halt-agents", "halt-all"):
            return
        try:
            self._agent_api_request(
                "/stop",
                method="POST",
                body={"reason": f"avd-buddy kill switch tripped to {position} by {operator_claim.get('name', 'operator')}"},
                operator_claim=operator_claim,
                timeout=3.0,
            )
        except Exception:
            pass

    def _apply_voice_check(
        self,
        *,
        question: str,
        humor: str,
        events: set[str],
        answer: str,
        raw_aside: str | None,
    ) -> tuple[str, str | None]:
        turn_check = voice_check.Turn(
            events=events,
            operator_text=question,
            language="en",
            humor_setting="off" if humor == "off" else "light",
            replies_since_aside=self.replies_since_aside,
        )
        mode = voice_check.get_mode(turn_check, self.voice_settings)
        final_aside: str | None = None
        estate_names = ["contoso", "hp-contoso-prod", "hp-contoso-sales", "sh-pooled-03", "sh-sales-01"]
        if raw_aside and voice_check.aside_allowed(turn_check, self.voice_settings):
            findings = voice_check.check_reply(
                turn_check,
                raw_aside,
                self.voice_settings,
                estate_names=estate_names,
            )
            if not findings:
                final_aside = raw_aside
                self.replies_since_aside = 0
            else:
                self.replies_since_aside += 1
        else:
            self.replies_since_aside += 1
        return mode, final_aside

    def _record_history_item(self, turn_id: str, question: str) -> None:
        summary = " ".join(question.strip().split())
        if len(summary) > 52:
            summary = summary[:49].rstrip() + "..."
        self.history.insert(
            0,
            {
                "id": f"thread-{turn_id}",
                "title": summary or "Conversation turn",
                "updatedAt": "Just now",
                "presetKey": "",
            },
        )
        del self.history[20:]

    def start_turn(self, question: str, humor: str, operator_claim: dict[str, str]) -> dict[str, Any]:
        if self.sim_mode or not self.agent_api_url:
            return self.build_reply(question, humor, operator_claim)

        turn_id = "turn-" + uuid.uuid4().hex[:10]
        try:
            status, payload = self._agent_api_request(
                "/ask",
                method="POST",
                body={
                    "question": question,
                    "model": self.agent_model,
                    "identity": self.agent_identity_app_id or "buddy-live",
                },
                operator_claim=operator_claim,
                timeout=5.0,
            )
        except Exception:
            status, payload = 502, {"error": "agent-api-unreachable"}

        if status not in (200, 202) or not payload.get("id"):
            events = {"run-failed"}
            if self.switch_position != "run":
                events.add("kill-switch")
            answer = (
                "Nothing has changed in your estate. avd-buddy could not start a turn on the agent API "
                f"(status {status}). Safe next step: check the Container App logs or run the read-only Cloud Shell check."
            )
            mode, _ = self._apply_voice_check(
                question=question,
                humor=humor,
                events=events,
                answer=answer,
                raw_aside=None,
            )
            turn_obj: dict[str, Any] = {
                "id": turn_id,
                "operatorText": question,
                "requestedBy": operator_claim.get("name", "operator@contoso.com"),
                "mode": mode,
                "events": sorted(events),
                "answer": answer,
                "citations": [],
                "learnLinks": [],
                "aside": None,
                "plan": None,
                "status": "completed",
            }
            self.turns[turn_id] = turn_obj
            return turn_obj

        turn_obj = {
            "id": turn_id,
            "jobId": str(payload["id"]),
            "operatorText": question,
            "humor": humor,
            "requestedBy": operator_claim.get("name", "operator@contoso.com"),
            "mode": "light",
            "events": [],
            "answer": "",
            "citations": [],
            "learnLinks": [],
            "aside": None,
            "plan": None,
            "status": "running",
        }
        self.turns[turn_id] = turn_obj
        return turn_obj

    def poll_turn(self, turn_id: str, operator_claim: dict[str, str]) -> dict[str, Any] | None:
        turn_obj = self.turns.get(turn_id)
        if not turn_obj:
            return None
        if turn_obj.get("status") != "running" or self.sim_mode or not self.agent_api_url:
            return turn_obj

        job_id = turn_obj.get("jobId", "")
        if not job_id:
            turn_obj["status"] = "completed"
            return turn_obj

        try:
            status, payload = self._agent_api_request(
                f"/ask/{urllib.parse.quote(job_id, safe='')}",
                method="GET",
                operator_claim=operator_claim,
                timeout=4.0,
            )
        except Exception:
            return turn_obj

        if status != 200:
            return turn_obj

        job_status = payload.get("status")
        if job_status == "running":
            return turn_obj

        question = str(turn_obj.get("operatorText", ""))
        humor = str(turn_obj.get("humor", "light"))
        events: set[str] = set()
        if self.switch_position != "run":
            events.add("kill-switch")

        if job_status == "failed":
            err = payload.get("error") or {}
            step = str(err.get("step", "agent"))
            msg = str(err.get("message", "The agent API reported a failure."))
            if step == "spend-meter":
                events.add("spend-cap")
            else:
                events.add("run-failed")
            answer = f"Nothing has changed in your estate. The agent step `{step}` stopped: {msg}"
            mode, _ = self._apply_voice_check(
                question=question,
                humor=humor,
                events=events,
                answer=answer,
                raw_aside=None,
            )
            turn_obj.update(
                {
                    "mode": mode,
                    "events": sorted(events),
                    "answer": answer,
                    "citations": [],
                    "learnLinks": [],
                    "aside": None,
                    "plan": None,
                    "status": "completed",
                }
            )
            self._record_history_item(turn_id, question)
            return turn_obj

        result = payload.get("result") or {}
        answer = str(result.get("answer", "")).strip() or "No findings returned."
        raw_aside = result.get("aside") if isinstance(result.get("aside"), str) else None
        plan = result.get("plan") if isinstance(result.get("plan"), dict) else None
        if plan is not None:
            events.update({"plan-shown", "approval-pending"})

        evidence = result.get("evidence") or {}
        citations: list[dict[str, str]] = []
        cite_block = evidence.get("citations") or {}
        verified_refs = cite_block.get("verified") if "verified" in cite_block else cite_block.get("cited")
        for idx, ref in enumerate(verified_refs or [], start=1):
            citations.append({"id": f"cite-{idx:02d}", "label": str(ref)})
        for idx, tool in enumerate(evidence.get("toolCalls") or [], start=1):
            if isinstance(tool, dict):
                name = str(tool.get("name", "tool"))
            else:
                name = str(tool)
            if name and not any(c["label"] == name for c in citations):
                citations.append({"id": f"call-{idx:02d}", "label": name})

        claim_check_info = evidence.get("claimCheck") if isinstance(evidence.get("claimCheck"), dict) else None
        if claim_check_info:
            unsupported = claim_check_info.get("unsupported") or []
            if unsupported and "not confirmed" not in answer.lower():
                unconfirmed_labels = [str(u.get("claim", "")).strip() for u in unsupported if u.get("claim")]
                if unconfirmed_labels:
                    answer = f"{answer}\n\nNot confirmed: {'; '.join(unconfirmed_labels)}."

        mode, final_aside = self._apply_voice_check(
            question=question,
            humor=humor,
            events=events,
            answer=answer,
            raw_aside=raw_aside,
        )
        turn_obj.update(
            {
                "mode": mode,
                "events": sorted(events),
                "answer": answer,
                "citations": citations,
                "claimCheck": claim_check_info,
                "learnLinks": result.get("learnLinks") if isinstance(result.get("learnLinks"), list) else [],
                "aside": final_aside,
                "plan": plan,
                "status": "completed",
            }
        )
        self._record_history_item(turn_id, question)
        return turn_obj

    def build_reply(self, question: str, humor: str, operator_claim: dict[str, str]) -> dict[str, Any]:
        q_lower = question.lower()
        events: set[str] = set()
        plan: dict[str, Any] | None = None
        raw_aside: str | None = "Those two enjoyed a quieter weekend than most of us."

        if self.switch_position != "run":
            events.add("kill-switch")

        if any(w in q_lower for w in ("drain", "restart", "approve", "plan")):
            events.update({"plan-shown", "approval-pending"})
            plan = DEFAULT_FIXTURE_PLAN
            answer = (
                "I drafted plan `plan-20261108-01` to enable drain mode on `sh-pooled-03` (`hp-contoso-prod`) and restart its AVD agent service. "
                "Approving runs this exact plan once in GitHub; the rollback command is listed on the card."
            )
            citations = [
                {"id": "call-mcp-02", "label": "virtualdesktop_sessionhost_list (hp-contoso-prod)"},
                {"id": "call-kql-02", "label": "WVDAgentHealthStatus (sh-pooled-03, 0 active sessions)"},
            ]
            learn_links = [
                {"title": "Microsoft Learn: Drain mode in Azure Virtual Desktop", "url": "https://learn.microsoft.com/azure/virtual-desktop/drain-mode"},
                {"title": "GitHub Docs: Reviewing deployments", "url": "https://docs.github.com/actions/how-tos/deploy/configure-and-manage-deployments/review-deployments"},
            ]
            raw_aside = "That agent just needed a coffee break."
        elif any(w in q_lower for w in ("defender", "security", "breach")):
            events.add("security-finding")
            answer = (
                "Microsoft Defender for Cloud reported a High severity alert on `sh-pooled-05` (`Suspicious PowerShell command line`). "
                "Nothing has been modified by avd-buddy. Safe first step: put `sh-pooled-05` in drain mode while you inspect the host's security event log."
            )
            citations = [{"id": "call-sec-01", "label": "SecurityAlert (High severity, sh-pooled-05)"}]
            learn_links = [{"title": "Microsoft Learn: Security alerts in Defender for Cloud", "url": "https://learn.microsoft.com/azure/defender-for-cloud/alerts-reference"}]
            raw_aside = "Never a dull moment."
        elif any(w in q_lower for w in ("fail", "error", "urgent", "down", "stuck")):
            events.add("run-failed")
            answer = (
                "Nothing has changed in your session host pools: the deployment stopped at the network validation step before any VM was created. "
                "The error states that subnet `snet-avd-hosts` (`10.20.1.0/24`) overlaps an existing subnet in `vnet-contoso-spoke`. "
                "Next step: run the preflight check with `-Fix` off in Cloud Shell and paste its last line here."
            )
            citations = [{"id": "call-deploy-01", "label": "ARM deployment operation: NetcfgInvalidSubnet"}]
            learn_links = [{"title": "Microsoft Learn: Troubleshoot subnet overlap errors", "url": "https://learn.microsoft.com/azure/virtual-network/virtual-networks-faq"}]
            raw_aside = "Subnets do not like sharing their personal space."
        else:
            answer = (
                "Two hosts were idle all weekend: `sh-sales-01` and `sh-sales-02` in `hp-contoso-sales` had zero sessions from Friday 18:00 UTC to Monday 08:00 UTC. "
                "Why it matters: a running VM bills compute every hour even with zero sessions, whereas a stopped (deallocated) VM stops compute billing while its OS disk keeps billing."
            )
            citations = [
                {"id": "call-kql-01", "label": "WVDAgentHealthStatus & WVDConnections (48h window)"},
                {"id": "call-cost-01", "label": "Cost Management: forecast_costs (rg-contoso-prod-avd)"},
            ]
            learn_links = [
                {"title": "Microsoft Learn: Azure VM states and billing", "url": "https://learn.microsoft.com/azure/virtual-machines/states-billing"},
            ]

        mode, final_aside = self._apply_voice_check(
            question=question,
            humor=humor,
            events=events,
            answer=answer,
            raw_aside=raw_aside,
        )
        turn_id = "turn-" + uuid.uuid4().hex[:10]
        turn_obj: dict[str, Any] = {
            "id": turn_id,
            "operatorText": question,
            "requestedBy": operator_claim.get("name", "operator@contoso.com"),
            "mode": mode,
            "events": sorted(events),
            "answer": answer,
            "citations": citations,
            "learnLinks": learn_links,
            "aside": final_aside,
            "plan": plan,
            "status": "completed",
        }
        self.turns[turn_id] = turn_obj
        return turn_obj


def make_handler(state: BuddyState) -> type[BaseHTTPRequestHandler]:
    class BuddyRequestHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            # Quiet in tests unless BUDDY_VERBOSE is set
            if os.environ.get("BUDDY_VERBOSE") == "1":
                super().log_message(fmt, *args)

        def _send_security_headers(self) -> None:
            self.send_header("Content-Security-Policy", CSP_HEADER)
            self.send_header("Referrer-Policy", REFERRER_POLICY)
            self.send_header("Permissions-Policy", PERMISSIONS_POLICY)
            self.send_header("X-Content-Type-Options", "nosniff")

        def _send_json(self, status_code: int, payload: dict[str, Any], *, no_store: bool = True) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status_code)
            self._send_security_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if no_store:
                self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _require_principal(self) -> dict[str, str] | None:
            principal = parse_client_principal(dict(self.headers))
            if principal:
                return principal
            if state.require_easy_auth:
                self._send_json(401, {"error": "unauthenticated", "loginUrl": "/.auth/login/aad"})
                return None
            return {"oid": "sim-operator", "name": "operator@contoso.com"}

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path

            if path == "/api/status":
                if state.switch_position == "halt-all":
                    self._send_json(503, {"error": "halt-all", "message": "avd-buddy can't reach its service."})
                    return
                principal = self._require_principal()
                if principal is None:
                    return
                state.sync_status_from_agent_api(principal)
                self._send_json(
                    200,
                    {
                        "switchPosition": state.switch_position,
                        "switchMeaning": SWITCH_MEANINGS.get(state.switch_position, ""),
                        "tokenSpendUsd": state.token_spend_usd,
                        "tokenCapUsd": state.token_cap_usd,
                        "operator": principal["name"],
                        "simMode": state.sim_mode,
                        "waitingItems": [
                            {
                                "id": "wait-plan-01",
                                "kind": "plan",
                                "title": "Plan plan-20261108-01 waiting for GitHub approval (hosts.drain-and-restart-agent)",
                                "threadId": "thread-drain-host-03",
                            }
                        ],
                        "estate": state.estate_status,
                    },
                )
                return

            if path == "/api/history":
                if state.switch_position == "halt-all":
                    self._send_json(503, {"error": "halt-all"})
                    return
                principal = self._require_principal()
                if principal is None:
                    return
                self._send_json(200, {"items": state.history})
                return

            if path.startswith("/api/turns/"):
                if state.switch_position == "halt-all":
                    self._send_json(503, {"error": "halt-all"})
                    return
                principal = self._require_principal()
                if principal is None:
                    return
                turn_id = path.split("/api/turns/", 1)[1].strip("/")
                qs = urllib.parse.parse_qs(parsed.query)
                try:
                    after = int((qs.get("after") or ["0"])[0])
                except ValueError:
                    after = 0
                turn_obj = state.poll_turn(turn_id, principal)
                if not turn_obj:
                    self._send_json(404, {"error": "turn-not-found"})
                    return
                done = turn_obj.get("status") == "completed"
                self._send_json(200, {"turnId": turn_id, "done": done, "nextAfter": after + 1, "turn": turn_obj})
                return

            # Serve static files from the pre-indexed allowlist table only
            entry = state.static_files.get(path)
            if entry is None:
                if not path.startswith("/api/") and not path.startswith("/.auth/"):
                    entry = state.static_files.get("/index.html")
                if entry is None:
                    self._send_json(404, {"error": "not-found"})
                    return

            content_type, data = entry
            self.send_response(200)
            self._send_security_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path

            if state.switch_position == "halt-all":
                self._send_json(503, {"error": "halt-all", "message": "avd-buddy can't reach its service."})
                return

            principal = self._require_principal()
            if principal is None:
                return

            length = int(self.headers.get("Content-Length", "0") or "0")
            raw_body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                body = json.loads(raw_body.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid-json"})
                return

            if path == "/api/turns":
                if state.switch_position in ("halt-agents", "halt-all"):
                    self._send_json(409, {"error": "halted", "switchPosition": state.switch_position})
                    return
                question = str(body.get("question", "")).strip()
                if not question:
                    self._send_json(400, {"error": "empty-question"})
                    return
                humor = str(body.get("humor", "light"))
                turn_obj = state.start_turn(question, humor, principal)
                http_code = 202 if turn_obj.get("status") == "running" else 200
                self._send_json(http_code, {"turnId": turn_obj["id"], "status": turn_obj["status"], "turn": turn_obj})
                return

            if path == "/api/switch/trip":
                position = str(body.get("position", "")).strip()
                confirm = str(body.get("confirm", "")).strip()
                # Governance section 2.3 & buddy-app.md section 3: can trip to halt positions only, never reset to 'run'.
                if position not in VALID_HALT_POSITIONS:
                    self._send_json(400, {"error": "invalid-halt-position", "allowed": list(VALID_HALT_POSITIONS)})
                    return
                if confirm != position:
                    self._send_json(400, {"error": "confirmation-mismatch"})
                    return
                state.switch_position = position
                state.trip_agent_api_stop(position, principal)
                self._send_json(
                    200,
                    {
                        "switchPosition": state.switch_position,
                        "switchMeaning": SWITCH_MEANINGS[position],
                        "trippedBy": principal["name"],
                    },
                )
                return

            self._send_json(404, {"error": "not-found"})

    return BuddyRequestHandler


def main(argv: list[str]) -> int:
    build_buddy_icons.build_icons(ROOT)
    port = int(os.environ.get("PORT", "8080"))
    require_auth = os.environ.get("BUDDY_REQUIRE_EASY_AUTH", "false").lower() == "true"
    sim_mode = os.environ.get("BUDDY_SIM_MODE", "true").lower() == "true"
    switch_pos = os.environ.get("KILL_SWITCH_POSITION", "run")
    cap_usd = float(os.environ.get("TOKEN_SPEND_CAP_USD", "15.0"))
    state = BuddyState(
        require_easy_auth=require_auth,
        sim_mode=sim_mode,
        switch_position=switch_pos,
        token_cap_usd=cap_usd,
        agent_api_url=os.environ.get("AGENT_API_URL", ""),
        agent_model=os.environ.get("AGENT_MODEL", "claude-sonnet-5-5"),
        agent_identity_app_id=os.environ.get("AGENT_IDENTITY_APP_ID", ""),
        mcp_app_id=os.environ.get("MCP_APP_ID", ""),
        buddy_uami_client_id=os.environ.get("BUDDY_UAMI_CLIENT_ID", ""),
    )
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(state))
    print(f"avd-buddy listening on http://0.0.0.0:{port} (sim_mode={sim_mode}, require_easy_auth={require_auth})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
