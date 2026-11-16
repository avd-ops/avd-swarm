# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Offline tests for avd_gate signed co-signature verification and avd-buddy /plans/* & POST-only trip auth."""

from __future__ import annotations

import base64
import http.client
import json
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "gate", ROOT / "buddy"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from avd_gate import apply as gate_apply  # noqa: E402
from avd_gate.cosign import sign_cosignature, verify_cosignature  # noqa: E402
import server as buddy_server  # noqa: E402


class CosignVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
        self.issued = (self.now - timedelta(minutes=2)).isoformat()
        self.expires = (self.now + timedelta(minutes=30)).isoformat()
        self.full_hash = "a" * 64
        self.channel_keys = {"teams": "test-teams-hmac-key", "servicenow": "test-snow-hmac-key"}
        self.allowlist = ["oid-operator-1"]

    def test_valid_cosignature_passes_and_invalid_forms_fail(self) -> None:
        sig = sign_cosignature(
            plan_id="plan-cs-01",
            full_plan_hash=self.full_hash,
            signer_oid="oid-operator-1",
            channel="teams",
            issued_at=self.issued,
            expires_at=self.expires,
            channel_key=self.channel_keys["teams"],
        )
        self.assertEqual(
            verify_cosignature(
                sig,
                expected_plan_id="plan-cs-01",
                expected_full_plan_hash=self.full_hash,
                authorized_signer_oids=self.allowlist,
                channel_keys=self.channel_keys,
                now_utc=self.now,
            ),
            [],
        )

        # Reject 8-char display prefix
        prefix_sig = {**sig, "fullPlanHash": self.full_hash[:8]}
        reasons = verify_cosignature(
            prefix_sig,
            expected_plan_id="plan-cs-01",
            expected_full_plan_hash=self.full_hash,
            authorized_signer_oids=self.allowlist,
            channel_keys=self.channel_keys,
            now_utc=self.now,
        )
        self.assertTrue(any("64-char" in r for r in reasons))

        # Reject static unauthenticated webhook
        static_sig = {**sig, "authMethod": "static_webhook"}
        reasons = verify_cosignature(
            static_sig,
            expected_plan_id="plan-cs-01",
            expected_full_plan_hash=self.full_hash,
            authorized_signer_oids=self.allowlist,
            channel_keys=self.channel_keys,
            now_utc=self.now,
        )
        self.assertTrue(any("static" in r for r in reasons))

        # Reject unauthorized signerOid
        bad_oid = sign_cosignature(
            plan_id="plan-cs-01",
            full_plan_hash=self.full_hash,
            signer_oid="oid-attacker",
            channel="teams",
            issued_at=self.issued,
            expires_at=self.expires,
            channel_key=self.channel_keys["teams"],
        )
        reasons = verify_cosignature(
            bad_oid,
            expected_plan_id="plan-cs-01",
            expected_full_plan_hash=self.full_hash,
            authorized_signer_oids=self.allowlist,
            channel_keys=self.channel_keys,
            now_utc=self.now,
        )
        self.assertTrue(any("allowlist" in r for r in reasons))

        # Reject expired co-signature
        expired_sig = sign_cosignature(
            plan_id="plan-cs-01",
            full_plan_hash=self.full_hash,
            signer_oid="oid-operator-1",
            channel="teams",
            issued_at=(self.now - timedelta(minutes=40)).isoformat(),
            expires_at=(self.now - timedelta(minutes=1)).isoformat(),
            channel_key=self.channel_keys["teams"],
        )
        reasons = verify_cosignature(
            expired_sig,
            expected_plan_id="plan-cs-01",
            expected_full_plan_hash=self.full_hash,
            authorized_signer_oids=self.allowlist,
            channel_keys=self.channel_keys,
            now_utc=self.now,
        )
        self.assertTrue(any("expired" in r for r in reasons))

    def test_apply_approved_plan_enforces_cosign_when_required(self) -> None:
        plan = {
            "planId": "plan-cs-apply",
            "action": "host.drain",
            "cloud": "commercial",
            "targetSurface": "avd",
            "targetScope": "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-avd-prod/providers/Microsoft.DesktopVirtualization/hostPools/hp-01/sessionHosts/sh-01",
            "evidence": [{"threadStartedBy": "avd-buddy"}],
            "why": {"kind": "fix", "decision": "0001"},
            "rollback": {"restoreAllowNewSession": True},
            "requireCosign": True,
        }
        state = {
            "cloud": "commercial",
            "targetDigest": "sha256:sh-01-v1",
            "targetScope": plan["targetScope"],
            "allowNewSession": True,
            "heartbeatFresh": True,
            "activeSessions": 0,
            "recentConnectionsInWindow": False,
            "powerLocked": False,
            "scalingExcluded": False,
            "powerState": "running",
            "availableHostsAfterAction": 2,
            "requiredAvailableHostsFloor": 2,
            "inFlightPlanOnPool": None,
            "serviceHealthClear": True,
            "hostsInPlan": 1,
            "hostsTargetedOnPool": 1,
            "usersChangedCount": 0,
            "totalPlansInLastHour": 1,
            "writesInLast24h": 2,
            "spendEstimate": {
                "estimatedUsd": 0.50,
                "monthToDateUsd": 12.00,
                "pendingUsd": 0.00,
                "unpricedCount": 0,
                "openRunsOnSandbox": 0,
            },
            "inBusinessHours": False,
        }
        approved_hash = gate_apply.canonical_plan_hash(plan, state)
        cfg = {
            "authorizedSignerOids": self.allowlist,
            "allowedChannels": ["teams"],
            "channelKeys": self.channel_keys,
        }

        # Missing co-signature refuses when requireCosign is True
        refused = gate_apply.apply_approved_plan(
            plan,
            approved_hash,
            read_flag=lambda: "run",
            read_live_state=lambda: state,
            apply_action=lambda _p, _s: {"applied": True},
            verify_action=lambda _p, _s, _e: True,
            root=ROOT,
            cosign_config=cfg,
            now_utc=self.now,
        )
        self.assertEqual(refused["status"], "refused")

        # Valid co-signature succeeds
        sig = sign_cosignature(
            plan_id="plan-cs-apply",
            full_plan_hash=approved_hash,
            signer_oid="oid-operator-1",
            channel="teams",
            issued_at=self.issued,
            expires_at=self.expires,
            channel_key=self.channel_keys["teams"],
        )
        applied = gate_apply.apply_approved_plan(
            plan,
            approved_hash,
            read_flag=lambda: "run",
            read_live_state=lambda: state,
            apply_action=lambda _p, _s: {"applied": True},
            verify_action=lambda _p, _s, _e: True,
            root=ROOT,
            cosignature=sig,
            cosign_config=cfg,
            now_utc=self.now,
        )
        self.assertEqual(applied["status"], "applied-and-verified")


class BuddyPlanAndTripAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = buddy_server.BuddyState(sim_mode=True, require_easy_auth=True)
        self.server = HTTPServer(("127.0.0.1", 0), buddy_server.make_handler(self.state))
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def _req(self, method: str, path: str, headers: dict[str, str] | None = None, body: bytes | None = None) -> tuple[int, str, dict[str, str]]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", errors="replace")
        resp_headers = {k.lower(): v for k, v in resp.getheaders()}
        conn.close()
        return resp.status, raw, resp_headers

    def test_plans_route_enforces_easy_auth_before_any_html_or_meta_tags(self) -> None:
        # Unauthenticated unfurler / browser gets 401 JSON with zero <meta> / og: tags
        status, body, headers = self._req("GET", "/plans/plan-123")
        self.assertEqual(status, 401)
        self.assertNotIn("<meta", body.lower())
        self.assertNotIn("og:", body.lower())
        self.assertEqual(headers.get("cache-control"), "no-store")

        # Authenticated request gets 200 HTML
        principal = base64.b64encode(
            json.dumps({"userId": "oid-1", "userDetails": "operator@contoso.com"}).encode("utf-8")
        ).decode("ascii")
        status_ok, body_ok, headers_ok = self._req(
            "GET", "/plans/plan-123", headers={"X-MS-CLIENT-PRINCIPAL": principal}
        )
        self.assertEqual(status_ok, 200)
        self.assertIn("<html", body_ok.lower())
        self.assertEqual(headers_ok.get("cache-control"), "no-store")

    def test_trip_is_post_only_behind_easy_auth(self) -> None:
        # GET /api/switch/trip and GET /trip return 405 Method Not Allowed
        for p in ("/api/switch/trip", "/trip"):
            status, _, headers = self._req("GET", p)
            self.assertEqual(status, 405)
            self.assertEqual(headers.get("allow"), "POST")

        # Unauthenticated POST /api/switch/trip returns 401
        status_unauth, _, _ = self._req(
            "POST",
            "/api/switch/trip",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"position": "halt-actions", "confirm": "halt-actions"}).encode("utf-8"),
        )
        self.assertEqual(status_unauth, 401)


if __name__ == "__main__":
    unittest.main()
