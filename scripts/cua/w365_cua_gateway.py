"""Default-deny MCP gateway for the optional w365-agents adapter (mcp_W365ComputerUse).

Evaluates MCP tool calls against scripts/cua/policy.yml, enforces halt state
(allowing only EndSession when halted), logs redacted decisions and SHA-256
screenshot hashes to JSONL, and forwards allowed calls to Microsoft's
mcp_W365ComputerUse endpoint.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

DEFAULT_POLICY_PATH = Path(__file__).with_name("policy.yml")
SERVER_URL_TEMPLATE = (
    "https://agent365.svc.cloud.microsoft/agents/tenants/{tenant_id}/servers/mcp_W365ComputerUse"
)


def load_policy(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_POLICY_PATH
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid CUA gateway policy at {target}")
    return data


def _url_allowed(url: str, allowed_urls: list[str]) -> bool:
    if not isinstance(url, str) or not url.strip():
        return False
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or not parsed.netloc:
        return False
    return any(url.strip().startswith(prefix) for prefix in allowed_urls)


def evaluate_tool_call(
    tool: str,
    arguments: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    halted: bool = False,
) -> tuple[bool, str]:
    """Default-deny policy check for a single MCP tool call."""
    pol = policy if policy is not None else load_policy()
    args = arguments or {}

    if halted or os.environ.get("WARDEN_HALT") == "1":
        if tool == "EndSession":
            return True, "halted: EndSession permitted for clean teardown"
        return False, "halted: gateway is halted"

    deny_list = set(pol.get("deny") or [])
    allow_list = set(pol.get("allow") or [])

    if tool in deny_list:
        return False, f"denied by policy: {tool}"
    if tool not in allow_list:
        return False, f"default-deny: {tool} is not in allowlist"

    if tool == "browser_navigate":
        url = str(args.get("url") or "")
        allowed_urls = list(pol.get("allowedUrls") or [])
        if not _url_allowed(url, allowed_urls):
            return False, "denied: url is outside allowedUrls"

    return True, "allowed"


def hash_screenshot(raw_bytes: bytes | str) -> str:
    data = raw_bytes.encode("utf-8") if isinstance(raw_bytes, str) else raw_bytes
    return "sha256:" + hashlib.sha256(data).hexdigest()


def log_decision(
    log_path: Path,
    tool: str,
    allowed: bool,
    reason: str,
    screenshot_bytes: bytes | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "allowed": allowed,
        "reason": reason,
    }
    if screenshot_bytes is not None:
        entry["screenshotHash"] = hash_screenshot(screenshot_bytes)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def forward_mcp_call(
    tenant_id: str,
    bearer_token: str,
    tool: str,
    arguments: dict[str, Any] | None = None,
    request_id: int = 1,
    timeout: int = 30,
) -> dict[str, Any]:
    """Forwards an already-allowed MCP tool call to mcp_W365ComputerUse."""
    if not tenant_id or not bearer_token:
        raise ValueError("tenant_id and bearer_token are required")
    url = SERVER_URL_TEMPLATE.format(tenant_id=tenant_id)
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments or {}},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))
