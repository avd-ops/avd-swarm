#!/usr/bin/env python3
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Tests for avd-buddy installable web app and Container App server (docs/buddy-app.md section 12, decision 0010)."""

from __future__ import annotations

import base64
import gzip
import json
import re
import shutil
import struct
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
for folder in (ROOT / "scripts", ROOT / "buddy", ROOT / "runner", ROOT / "spikes/s1a/agent_api"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import build_buddy_icons  # noqa: E402
import runbook_endpoint  # noqa: E402
import server as buddy_server  # noqa: E402


def _read_png_ihdr(path: Path) -> tuple[int, int, str]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError(f"Not a valid PNG: {path}")
    width, height, _bit_depth, color_type = struct.unpack(">IIBB", data[16:26])
    mode = "RGBA" if color_type == 6 else ("RGB" if color_type == 2 else str(color_type))
    return width, height, mode


class ManifestAndIconsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.icons_dir = build_buddy_icons.build_icons(ROOT)

    def test_manifest_fields_and_token_hex_match(self) -> None:
        manifest_path = ROOT / "buddy/app/manifest.webmanifest"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "avd-buddy")
        self.assertEqual(manifest["short_name"], "buddy")
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["id"], "/")

        # Verify background_color and theme_color match --brand-ink in avd-ops.css, and light meta matches --bg.
        css_text = (ROOT / "buddy/app/assets/avd-ops.css").read_text(encoding="utf-8")
        brand_ink_match = re.search(r"--brand-ink:\s*(#[0-9a-fA-F]{6})", css_text)
        bg_light_match = re.search(r":root\s*\{[^}]*--bg:\s*(#[0-9a-fA-F]{6})", css_text)
        self.assertIsNotNone(brand_ink_match)
        self.assertIsNotNone(bg_light_match)
        brand_ink = brand_ink_match.group(1).lower()
        bg_light = bg_light_match.group(1).lower()

        self.assertEqual(manifest["background_color"].lower(), brand_ink)
        self.assertEqual(manifest["theme_color"].lower(), brand_ink)

        html_text = (ROOT / "buddy/app/index.html").read_text(encoding="utf-8")
        self.assertIn(f'media="(prefers-color-scheme: light)" content="{bg_light}"', html_text)
        self.assertIn(f'media="(prefers-color-scheme: dark)" content="{brand_ink}"', html_text)
        self.assertIn("viewport-fit=cover", html_text)
        self.assertIn("interactive-widget=resizes-content", html_text)

    def test_generated_icons_exist_at_every_size_and_mode(self) -> None:
        expected_pngs = {
            "icon-192.png": (192, 192, "RGBA"),
            "icon-512.png": (512, 512, "RGBA"),
            "icon-maskable-512.png": (512, 512, "RGB"),
            "icon-monochrome-512.png": (512, 512, "RGBA"),
            "apple-touch-icon.png": (180, 180, "RGB"),
            "favicon-32.png": (32, 32, "RGBA"),
        }
        for name, (w, h, mode) in expected_pngs.items():
            path = self.icons_dir / name
            self.assertTrue(path.is_file(), f"Missing generated icon {name}")
            self.assertEqual(_read_png_ihdr(path), (w, h, mode))

        import xml.etree.ElementTree as ET

        for svg_name in ("favicon.svg", "avatar.svg"):
            svg_path = self.icons_dir / svg_name
            self.assertTrue(svg_path.is_file(), f"Missing generated SVG {svg_name}")
            svg_text = svg_path.read_text(encoding="utf-8")
            self.assertIn("<svg", svg_text)
            ET.fromstring(svg_text)

    def test_mascot_fails_when_review_md_is_missing_and_placeholder_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            (tmp_root / "buddy/app").mkdir(parents=True)
            (tmp_root / "assets/buddy").mkdir(parents=True)
            shutil.copy(ROOT / "assets/buddy/avd-buddy-icon.svg", tmp_root / "assets/buddy/avd-buddy-icon.svg")
            (tmp_root / "buddy/app/brand.json").write_text('{"icon_source": "mascot"}\n', encoding="utf-8")

            # Missing assets/buddy/REVIEW.md must fail build_icons (docs/buddy-app.md section 6.2)
            with self.assertRaises(RuntimeError):
                build_buddy_icons.build_icons(tmp_root)

            # Switching to placeholder works without REVIEW.md (section 6.1)
            (tmp_root / "buddy/app/brand.json").write_text('{"icon_source": "placeholder"}\n', encoding="utf-8")
            out = build_buddy_icons.build_icons(tmp_root)
            self.assertTrue((out / "icon-512.png").is_file())


class StaticShellAndBudgetTests(unittest.TestCase):
    def test_service_worker_rules(self) -> None:
        sw_text = (ROOT / "buddy/app/sw.js").read_text(encoding="utf-8")
        self.assertIn('pathname.startsWith("/api/")', sw_text)
        self.assertIn('pathname.startsWith("/.auth/")', sw_text)
        self.assertIn("caches.delete", sw_text)
        self.assertIn("SKIP_WAITING", sw_text)

    def test_no_inline_styles_or_scripts_and_colors_only_through_tokens(self) -> None:
        from html.parser import HTMLParser

        class InlineCheckParser(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.inline_styles: list[str] = []
                self.script_srcs: list[str | None] = []
                self.in_script = False
                self.script_data: list[str] = []

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                attr_map = {k.lower(): v for k, v in attrs}
                if "style" in attr_map:
                    self.inline_styles.append(tag)
                if tag.lower() == "script":
                    self.in_script = True
                    self.script_srcs.append(attr_map.get("src"))

            def handle_endtag(self, tag: str) -> None:
                if tag.lower() == "script":
                    self.in_script = False

            def handle_data(self, data: str) -> None:
                if self.in_script and data.strip():
                    self.script_data.append(data.strip())

        html_text = (ROOT / "buddy/app/index.html").read_text(encoding="utf-8")
        parser = InlineCheckParser()
        parser.feed(html_text)
        self.assertEqual(parser.inline_styles, [])
        self.assertTrue(all(src for src in parser.script_srcs))
        self.assertEqual(parser.script_data, [])

        # app.css must use design system tokens only (no raw hex values)
        css_text = (ROOT / "buddy/app/app.css").read_text(encoding="utf-8")
        css_no_comments = re.sub(r"/\*.*?\*/", "", css_text, flags=re.DOTALL)
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b", css_no_comments))

    def test_no_guids_or_tokens_and_shell_js_within_60kb_compressed_budget(self) -> None:
        guid_re = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
        jwt_re = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")

        total_gz = 0
        for rel in ("index.html", "app.css", "app.js", "fixtures.js", "sw.js", "manifest.webmanifest"):
            path = ROOT / "buddy/app" / rel
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(guid_re.search(text), f"GUID found in {rel}")
            self.assertIsNone(jwt_re.search(text), f"JWT found in {rel}")
            if rel.endswith(".js"):
                total_gz += len(gzip.compress(text.encode("utf-8")))

        # Performance budget (docs/buddy-app.md section 9): shell JS compressed <= 60 KB
        self.assertLessEqual(total_gz, 60 * 1024)

    def test_front_end_only_no_in_app_approve_button_or_endpoint(self) -> None:
        for rel in ("buddy/app/index.html", "buddy/app/app.js", "buddy/server.py"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("/api/approve", text)
            self.assertNotIn("/api/plans/approve", text)
        app_js = (ROOT / "buddy/app/app.js").read_text(encoding="utf-8")
        self.assertIn("Review and approve in GitHub", app_js)
        self.assertIn('target = "_blank"', app_js)
        self.assertIn('rel = "noopener noreferrer"', app_js)

    def test_optional_voice_chat_controls_and_default_off(self) -> None:
        html_text = (ROOT / "buddy/app/index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "buddy/app/app.js").read_text(encoding="utf-8")
        self.assertIn('id="setting-voice"', html_text)
        self.assertIn('id="composer-mic"', html_text)
        self.assertIn('voice: "off"', app_js)
        self.assertIn("SpeechRecognition", app_js)
        self.assertIn("speechSynthesis", app_js)
        self.assertIn("msg-speak-btn", app_js)


class ServerAndIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not (ROOT / "buddy/app/icons/icon-512.png").is_file():
            build_buddy_icons.build_icons(ROOT)

    def setUp(self) -> None:
        self.state = buddy_server.BuddyState(require_easy_auth=False, sim_mode=True)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), buddy_server.make_handler(self.state))
        self.port = self.httpd.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        req_headers = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            req_headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base_url + path, data=data, headers=req_headers, method=method)
        # Bypass any environment HTTP proxy for localhost server tests
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=5) as resp:
                return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
        except urllib.error.HTTPError as err:
            return err.code, {k.lower(): v for k, v in err.headers.items()}, err.read()

    def test_csp_and_security_headers_present_on_every_route(self) -> None:
        for route in ("/", "/app.js", "/manifest.webmanifest", "/api/status", "/api/history"):
            status, headers, _ = self._request(route)
            self.assertEqual(status, 200, f"Unexpected status {status} on {route}")
            self.assertEqual(headers.get("content-security-policy"), buddy_server.CSP_HEADER)
            self.assertEqual(headers.get("referrer-policy"), "no-referrer")
            self.assertEqual(headers.get("permissions-policy"), "camera=(), microphone=(self), geolocation=()")

    def test_status_returns_phase_d_five_plane_estate_posture(self) -> None:
        status, _, raw = self._request("/api/status")
        self.assertEqual(status, 200)
        payload = json.loads(raw.decode("utf-8"))
        self.assertIn("estate", payload)
        estate = payload["estate"]
        self.assertEqual(
            [p["id"] for p in estate["planes"]],
            ["azure", "entra", "intune", "m365", "power-platform"],
        )
        self.assertIn("meterAudit", estate)
        self.assertIn("topFindings", estate)
        html = (buddy_server.APP_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="status-estate-card"', html)

    def test_light_mode_allows_clean_aside_and_humor_off_hides_it(self) -> None:
        status, _, raw = self._request(
            "/api/turns",
            method="POST",
            body={"question": "Which hosts were idle all weekend?", "humor": "light"},
        )
        self.assertEqual(status, 200)
        payload = json.loads(raw.decode("utf-8"))
        turn = payload["turn"]
        self.assertEqual(turn["mode"], "light")
        self.assertIsNotNone(turn["aside"])

        # Polling /api/turns/{id} returns the completed turn
        poll_status, _, poll_raw = self._request(f"/api/turns/{ payload['turnId'] }?after=0")
        self.assertEqual(poll_status, 200)
        poll_data = json.loads(poll_raw.decode("utf-8"))
        self.assertTrue(poll_data["done"])
        self.assertEqual(poll_data["turn"]["id"], payload["turnId"])

        # With humor: off, aside is stripped
        self.state.replies_since_aside = 99
        status2, _, raw2 = self._request(
            "/api/turns",
            method="POST",
            body={"question": "Which hosts were idle all weekend?", "humor": "off"},
        )
        self.assertEqual(status2, 200)
        turn2 = json.loads(raw2.decode("utf-8"))["turn"]
        self.assertIsNone(turn2["aside"])

    def test_hard_steady_plan_turn_renders_plan_and_strips_aside(self) -> None:
        self.state.replies_since_aside = 99
        status, _, raw = self._request(
            "/api/turns",
            method="POST",
            body={"question": "Drain host 3 and restart its agent", "humor": "light"},
        )
        self.assertEqual(status, 200)
        turn = json.loads(raw.decode("utf-8"))["turn"]
        self.assertEqual(turn["mode"], "hard-steady")
        # Section 12.7: hard-steady never shows an aside even when the fixture has one
        self.assertIsNone(turn["aside"])
        plan = turn["plan"]
        self.assertIsNotNone(plan)
        self.assertEqual(plan["planId"], "plan-20261108-01")
        self.assertEqual(len(plan["planHash"][:12]), 12)
        self.assertTrue(plan["approveUrl"].startswith("https://github.com/"))

    def test_kill_switch_trip_requires_confirmation_and_never_resets(self) -> None:
        # Attempting to reset to 'run' fails (no reset in avd-buddy)
        status, _, _ = self._request(
            "/api/switch/trip",
            method="POST",
            body={"position": "run", "confirm": "run"},
        )
        self.assertEqual(status, 400)

        # Mismatched confirmation fails
        status, _, _ = self._request(
            "/api/switch/trip",
            method="POST",
            body={"position": "halt-agents", "confirm": "wrong"},
        )
        self.assertEqual(status, 400)

        # Matching confirmation trips to halt-agents
        status, _, raw = self._request(
            "/api/switch/trip",
            method="POST",
            body={"position": "halt-agents", "confirm": "halt-agents"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw.decode("utf-8"))["switchPosition"], "halt-agents")

        # While at halt-agents, new turns are refused (409)
        status, _, _ = self._request(
            "/api/turns",
            method="POST",
            body={"question": "Which hosts were idle?", "humor": "light"},
        )
        self.assertEqual(status, 409)

        # Tripping to halt-all makes /api/status return 503
        status, _, _ = self._request(
            "/api/switch/trip",
            method="POST",
            body={"position": "halt-all", "confirm": "halt-all"},
        )
        self.assertEqual(status, 200)
        status, _, _ = self._request("/api/status")
        self.assertEqual(status, 503)

    def test_easy_auth_principal_enforcement(self) -> None:
        self.state.require_easy_auth = True
        status, _, _ = self._request("/api/status")
        self.assertEqual(status, 401)

        principal_payload = {
            "auth_typ": "aad",
            "claims": [
                {"typ": "preferred_username", "val": "alice@contoso.com"},
                {"typ": "oid", "val": "operator-claim-1"},
            ],
        }
        encoded = base64.b64encode(json.dumps(principal_payload).encode("utf-8")).decode("ascii")
        status, _, raw = self._request("/api/status", headers={"X-MS-CLIENT-PRINCIPAL": encoded})
        self.assertEqual(status, 200)
        data = json.loads(raw.decode("utf-8"))
        self.assertEqual(data["operator"], "alice@contoso.com")


class ContainerAppBicepTests(unittest.TestCase):
    def test_bicep_template_enforces_decision_0010(self) -> None:
        bicep_text = (ROOT / "templates/buddy/main.bicep").read_text(encoding="utf-8")
        self.assertIn("minReplicas: 0", bicep_text)
        self.assertIn("Microsoft.App/containerApps/authConfigs@2024-03-01", bicep_text)
        self.assertIn("override-use-mi-fic-assertion-clientid", bicep_text)
        self.assertIn("RedirectToLoginPage", bicep_text)
        self.assertIn("tokenStore: {\n        enabled: false\n      }", bicep_text)
        for env_name in ("BUDDY_UAMI_CLIENT_ID", "MCP_APP_ID", "AGENT_IDENTITY_APP_ID", "AGENT_MODEL"):
            self.assertIn(f"name: '{env_name}'", bicep_text)

        spike_bicep = (ROOT / "spikes/s1a/infra/main.bicep").read_text(encoding="utf-8")
        self.assertIn("param buddyCallerObjectId string", spike_bicep)
        self.assertIn("name: 'BUDDY_CALLER_OID'", spike_bicep)
        self.assertIn("output managedEnvironmentId string", spike_bicep)


class LiveAgentApiRelayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not (ROOT / "buddy/app/icons/icon-512.png").is_file():
            build_buddy_icons.build_icons(ROOT)

    def setUp(self) -> None:
        self.agent_jobs: dict[str, dict] = {}
        self.agent_meter = {"spentUsd": 3.75, "capUsd": 15.0, "stopped": False}
        self.last_auth_header = ""
        self.last_operator_oid = ""
        self.stop_reasons: list[str] = []
        test_case = self

        class MockAgentApiHandler(buddy_server.BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: object) -> None:
                pass

            def _send(self, code: int, payload: dict) -> None:
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:
                if self.path == "/healthz":
                    self._send(200, {"ok": True, "meter": test_case.agent_meter})
                    return
                if self.path.startswith("/ask/"):
                    job_id = self.path.split("/ask/", 1)[1]
                    job = test_case.agent_jobs.get(job_id)
                    if not job:
                        self._send(404, {"detail": "job not found"})
                        return
                    self._send(200, job)
                    return
                self._send(404, {"detail": "not found"})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0") or "0")
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
                test_case.last_auth_header = self.headers.get("Authorization", "")
                test_case.last_operator_oid = self.headers.get("X-Avd-Operator-Oid", "")
                if self.path == "/ask":
                    job_id = "job-live-01"
                    test_case.agent_jobs[job_id] = {
                        "status": "running",
                        "question": body.get("question", ""),
                    }
                    self._send(202, {"id": job_id})
                    return
                if self.path == "/stop":
                    test_case.agent_meter["stopped"] = True
                    test_case.stop_reasons.append(str(body.get("reason", "")))
                    self._send(200, {"stopped": True, "meter": test_case.agent_meter})
                    return
                self._send(404, {"detail": "not found"})

        self.agent_httpd = ThreadingHTTPServer(("127.0.0.1", 0), MockAgentApiHandler)
        agent_port = self.agent_httpd.server_address[1]
        self.agent_thread = threading.Thread(target=self.agent_httpd.serve_forever, daemon=True)
        self.agent_thread.start()

        self.state = buddy_server.BuddyState(
            require_easy_auth=False,
            sim_mode=False,
            agent_api_url=f"http://127.0.0.1:{agent_port}",
            token_provider=lambda: "mock-msi-token",
        )
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), buddy_server.make_handler(self.state))
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.agent_httpd.shutdown()
        self.agent_httpd.server_close()

    def _request(self, path: str, *, method: str = "GET", body: dict | None = None) -> tuple[int, dict]:
        headers = {}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read().decode("utf-8"))

    def test_allowed_buddy_caller_accepts_operator_or_buddy_uami_only(self) -> None:
        agent_api_dir = ROOT / "spikes/s1a/agent_api"
        if str(agent_api_dir) not in sys.path:
            sys.path.insert(0, str(agent_api_dir))
        import runbook_endpoint  # noqa: E402

        # Delegated operator token with Mcp.Tools.ReadWrite passes
        self.assertTrue(
            runbook_endpoint.allowed_buddy_caller(
                {"oid": "op-oid-1", "scp": "Mcp.Tools.ReadWrite"},
                operator_oid="op-oid-1",
                buddy_caller_oid="buddy-uami-oid-1",
            )
        )
        # avd-buddy Container App managed identity token (app-only, no scp) passes when buddy_caller_oid matches
        self.assertTrue(
            runbook_endpoint.allowed_buddy_caller(
                {"oid": "buddy-uami-oid-1", "idtyp": "app"},
                operator_oid="op-oid-1",
                buddy_caller_oid="buddy-uami-oid-1",
            )
        )
        # Unknown caller OID is rejected
        self.assertFalse(
            runbook_endpoint.allowed_buddy_caller(
                {"oid": "other-oid", "idtyp": "app"},
                operator_oid="op-oid-1",
                buddy_caller_oid="buddy-uami-oid-1",
            )
        )

    def test_live_relay_polls_running_to_done_and_enforces_voice_check(self) -> None:
        # 1. Status syncs spend and cap from agent_api /healthz
        status_code, status_data = self._request("/api/status")
        self.assertEqual(status_code, 200)
        self.assertFalse(status_data["simMode"])
        self.assertAlmostEqual(status_data["tokenSpendUsd"], 3.75)

        # 2. Starting a turn relays POST /ask with Bearer token and operator header, returning 202 running
        post_code, post_data = self._request(
            "/api/turns",
            method="POST",
            body={"question": "Which hosts were idle all weekend?", "humor": "light"},
        )
        self.assertEqual(post_code, 202)
        self.assertEqual(post_data["status"], "running")
        self.assertEqual(self.last_auth_header, "Bearer mock-msi-token")
        self.assertEqual(self.last_operator_oid, "sim-operator")
        turn_id = post_data["turnId"]

        # 3. First poll while job is still running returns done=False
        poll1_code, poll1_data = self._request(f"/api/turns/{turn_id}?after=0")
        self.assertEqual(poll1_code, 200)
        self.assertFalse(poll1_data["done"])
        self.assertEqual(poll1_data["nextAfter"], 1)

        # 4. Complete the job on agent_api with an aside that carries a forbidden number (must be stripped by voice_check)
        #    and an unsupported claim in evidence.claimCheck (must append "Not confirmed:")
        self.agent_jobs["job-live-01"] = {
            "status": "done",
            "result": {
                "answer": "Two hosts in hp-contoso-sales had zero sessions over the weekend [fact:idle-01].",
                "aside": "Those 2 hosts saved $14 in compute.",
                "evidence": {
                    "citations": {
                        "cited": ["fact:idle-01", "fact:unverified-99"],
                        "verified": ["fact:idle-01"],
                        "unverified": ["fact:unverified-99"],
                        "allVerified": False,
                    },
                    "claimCheck": {
                        "verdicts": [],
                        "supported": [{"claim": "Two hosts idle", "citations": ["fact:idle-01"]}],
                        "unsupported": [{"claim": "Autoscale is disabled", "citations": ["fact:unverified-99"]}],
                        "unchecked": [],
                        "mergeCheck": {"valid": False, "failingSentences": []},
                    },
                    "toolCalls": [{"name": "query_log_analytics"}],
                },
            },
        }
        poll2_code, poll2_data = self._request(f"/api/turns/{turn_id}?after=1")
        self.assertEqual(poll2_code, 200)
        self.assertTrue(poll2_data["done"])
        turn = poll2_data["turn"]
        self.assertEqual(turn["status"], "completed")
        self.assertIn("Two hosts in hp-contoso-sales", turn["answer"])
        self.assertIn("Not confirmed: Autoscale is disabled.", turn["answer"])
        # Aside had digits/currency so voice_check stripped it
        self.assertIsNone(turn["aside"])
        # Only verified citation + toolCall are included in citation chips (not unverified-99)
        self.assertEqual([c["label"] for c in turn["citations"]], ["fact:idle-01", "query_log_analytics"])
        self.assertIsNotNone(turn["claimCheck"])

        # 5. Tripping kill switch to halt-agents relays POST /stop to agent_api
        trip_code, _ = self._request(
            "/api/switch/trip",
            method="POST",
            body={"position": "halt-agents", "confirm": "halt-agents"},
        )
        self.assertEqual(trip_code, 200)
        self.assertEqual(len(self.stop_reasons), 1)
        self.assertIn("halt-agents", self.stop_reasons[0])


if __name__ == "__main__":
    unittest.main()
