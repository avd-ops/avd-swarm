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
from typing import Any

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
        {"gate": "Says why", "outcome": "pass", "text": "Pass: cites WVDAgentHealthStatus heartbeat gap and C15 fact ID"},
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
    ) -> None:
        self.require_easy_auth = require_easy_auth
        self.sim_mode = sim_mode
        self.switch_position = switch_position
        self.token_spend_usd = token_spend_usd
        self.token_cap_usd = token_cap_usd
        self.agent_api_url = agent_api_url.rstrip("/")
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

        turn_check = voice_check.Turn(
            events=events,
            operator_text=question,
            language="en",
            humor_setting="off" if humor == "off" else "light",
            replies_since_aside=self.replies_since_aside,
        )
        mode = voice_check.get_mode(turn_check, self.voice_settings)
        final_aside: str | None = None
        if raw_aside and voice_check.aside_allowed(turn_check, self.voice_settings):
            findings = voice_check.get_aside_findings(
                raw_aside,
                self.voice_settings,
                estate_names=["contoso", "hp-contoso-prod", "hp-contoso-sales", "sh-pooled-03", "sh-sales-01"],
            )
            if not findings:
                final_aside = raw_aside
                self.replies_since_aside = 0
            else:
                self.replies_since_aside += 1
        else:
            self.replies_since_aside += 1

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
                turn_obj = state.turns.get(turn_id)
                if not turn_obj:
                    self._send_json(404, {"error": "turn-not-found"})
                    return
                self._send_json(200, {"turnId": turn_id, "done": True, "nextAfter": 1, "turn": turn_obj})
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
                turn_obj = state.build_reply(question, humor, principal)
                self._send_json(200, {"turnId": turn_obj["id"], "status": "completed", "turn": turn_obj})
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
    state = BuddyState(
        require_easy_auth=require_auth,
        sim_mode=sim_mode,
        switch_position=switch_pos,
        agent_api_url=os.environ.get("AGENT_API_URL", ""),
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
