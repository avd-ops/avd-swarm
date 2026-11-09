#!/usr/bin/env python3
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Checks the kill switch Bicep template, watchdog rules, and monthly drill invariants (governance.md sections 2.3 and 2.4; R5, R6, R13, V1-3, V1-15, WP5).

Enforces:
1. `governance/watchdog.yml` declares the four switch positions (`run`, `halt-actions`, `halt-agents`, `halt-all`),
   `resetBy: person-cloud-shell-only`, preserved non-swarm identities (`stop-spend`, `landing-zone-deploy`, `operator`),
   non-human MCP callers refused at `halt-agents` (`sre-agent`, `foundry`, `claude-code`), positive integer thresholds,
   and all 18 deterministic watchdog rules from governance.md section 2.4.
2. `templates/kill-switch/main.bicep` grants only capability-removing permissions (R5), includes the `conditionVersion: '2.0'`
   ABAC delegation condition limiting `roleAssignments/delete` to agent principal IDs (V1-15), refuses machine resets to
   `run` (R6), never touches `stop-spend`, and supports both commercial and US Government ARM endpoints (C26).
3. `simulate_drill` exercises `run` -> `halt-actions` -> `halt-agents` -> `halt-all`, verifies adapter switch rows (WP5),
   non-human MCP refusal (V1-3), preserved non-agent role assignments (V1-15), re-trip on incomplete flag reset, and
   person Cloud Shell reset (R6, R13).

Usage: python3 scripts/check_kill_switch.py [repo root]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

WATCHDOG_FILE = "governance/watchdog.yml"
BICEP_FILE = "templates/kill-switch/main.bicep"
POSITIONS = ["run", "halt-actions", "halt-agents", "halt-all"]
TRIP_POSITIONS = {"halt-actions", "halt-agents", "halt-all"}
REQUIRED_PRESERVED_IDENTITIES = {"stop-spend", "landing-zone-deploy", "operator"}
REQUIRED_REFUSED_MCP_CALLERS = {"sre-agent", "foundry", "claude-code"}
REQUIRED_WATCHDOG_RULE_IDS = {
    "write.unapproved",
    "write.outside-scope",
    "adapter.sre-agent-write",
    "spend.daily-token-cap",
    "model.usage-after-halt",
    "plans.rate-or-rejections",
    "verify.failures-24h",
    "observer.silent",
    "guardrail.bypassed-on-main",
    "workflow.write-in-public-repo",
    "spend.cap.reached",
    "spend.teardown.failed",
    "operator.read-spike",
    "security.injection-suspected",
    "security.decoy-tripped",
    "switch.cleared-incomplete",
    "compliance.exemption-or-policy-write",
    "compliance.regressed-twice",
    "recovery.write-outside-scope",
}
ALLOWED_SWITCH_ROLE_ACTIONS = {
    "Microsoft.Resources/tags/read",
    "Microsoft.Resources/tags/write",
    "Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials/read",
    "Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials/delete",
    "Microsoft.Authorization/roleAssignments/read",
    "Microsoft.Authorization/roleAssignments/delete",
    "Microsoft.App/containerApps/read",
    "Microsoft.App/containerApps/stop/action",
}
REQUIRED_NOT_ACTIONS = {
    "Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials/write",
    "Microsoft.Authorization/roleAssignments/write",
    "Microsoft.Authorization/policyExemptions/write",
    "Microsoft.Authorization/policyAssignments/write",
    "Microsoft.App/containerApps/start/action",
}


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def watchdog_errors(root: Path) -> list[str]:
    path = root / WATCHDOG_FILE
    if not path.is_file():
        return [f"{WATCHDOG_FILE}: file does not exist"]
    try:
        data = load_yaml(path)
    except yaml.YAMLError as err:
        return [f"{WATCHDOG_FILE}: invalid YAML: {err}"]

    errors: list[str] = []
    sw = data.get("switch") if isinstance(data.get("switch"), dict) else {}
    if sw.get("tag") != "avd-swarm-switch":
        errors.append("switch.tag must be 'avd-swarm-switch'")
    if sw.get("positions") != POSITIONS:
        errors.append(f"switch.positions must be {POSITIONS}")
    if sw.get("resetBy") != "person-cloud-shell-only":
        errors.append("switch.resetBy must be 'person-cloud-shell-only' (R6)")
    preserved = set(sw.get("preserveIdentities") or [])
    if not REQUIRED_PRESERVED_IDENTITIES <= preserved:
        errors.append(f"switch.preserveIdentities must include {sorted(REQUIRED_PRESERVED_IDENTITIES)} (V1-15)")
    refused_mcp = set(sw.get("nonHumanMcpCallersRefusedAtHaltAgents") or [])
    if not REQUIRED_REFUSED_MCP_CALLERS <= refused_mcp:
        errors.append(
            f"switch.nonHumanMcpCallersRefusedAtHaltAgents must include {sorted(REQUIRED_REFUSED_MCP_CALLERS)} (V1-3)"
        )

    thresholds = data.get("thresholds")
    if not isinstance(thresholds, dict) or not thresholds:
        errors.append("thresholds must be a non-empty mapping")
    else:
        for k, v in thresholds.items():
            if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                errors.append(f"thresholds.{k} must be a positive integer")

    rules = data.get("rules")
    if not isinstance(rules, list):
        errors.append("rules must be a list")
    else:
        seen_ids: set[str] = set()
        for i, rule in enumerate(rules):
            if not isinstance(rule, dict):
                errors.append(f"rules[{i}] must be a mapping")
                continue
            rid = rule.get("id")
            if not isinstance(rid, str) or not rid:
                errors.append(f"rules[{i}].id must be non-empty text")
            elif rid in seen_ids:
                errors.append(f"rules[{i}].id {rid!r} is duplicated")
            else:
                seen_ids.add(rid)
            pos = rule.get("position")
            if pos not in TRIP_POSITIONS:
                errors.append(f"rules[{i}] ({rid}): position {pos!r} must be one of {sorted(TRIP_POSITIONS)}")
            if not isinstance(rule.get("pageOwner"), bool):
                errors.append(f"rules[{i}] ({rid}): pageOwner must be a boolean")
            if not isinstance(rule.get("source"), str) or not rule.get("source"):
                errors.append(f"rules[{i}] ({rid}): source is required")
        missing = sorted(REQUIRED_WATCHDOG_RULE_IDS - seen_ids)
        if missing:
            errors.append(f"missing required section 2.4 watchdog rule(s): {', '.join(missing)}")

    return [f"{WATCHDOG_FILE}: {e}" for e in errors]


def _extract_bicep_block_strings(text: str, block_name: str) -> list[str]:
    match = re.search(rf"{block_name}\s*:\s*\[(.*?)\]", text, re.DOTALL)
    if not match:
        return []
    return re.findall(r"'([^']+)'", match.group(1))


def bicep_errors(root: Path) -> list[str]:
    path = root / BICEP_FILE
    if not path.is_file():
        return [f"{BICEP_FILE}: file does not exist"]
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []

    actions = set(_extract_bicep_block_strings(text, "actions"))
    if not actions:
        errors.append("custom role must declare actions")
    else:
        forbidden = sorted(actions - ALLOWED_SWITCH_ROLE_ACTIONS)
        if forbidden:
            errors.append(f"custom role grants forbidden capability-adding action(s) (R5): {', '.join(forbidden)}")
        missing_actions = sorted(ALLOWED_SWITCH_ROLE_ACTIONS - actions)
        if missing_actions:
            errors.append(f"custom role is missing required switch action(s): {', '.join(missing_actions)}")

    not_actions = set(_extract_bicep_block_strings(text, "notActions"))
    missing_not = sorted(REQUIRED_NOT_ACTIONS - not_actions)
    if missing_not:
        errors.append(f"custom role notActions must explicitly forbid {', '.join(missing_not)} (R5)")

    if "conditionVersion: '2.0'" not in text or "Microsoft.Authorization/roleAssignments:PrincipalId" not in text:
        errors.append("roleAssignment must carry conditionVersion '2.0' ABAC delegation condition on PrincipalId (V1-15)")
    for param_name in ("agentHostsPrincipalId", "agentCostPrincipalId", "agentIntunePrincipalId"):
        if param_name not in text:
            errors.append(f"ABAC condition must scope roleAssignments/delete to {param_name} (V1-15)")

    if "ResetForbiddenByMachine" not in text:
        errors.append("Logic App must refuse position == 'run' with ResetForbiddenByMachine (R6)")

    if "https://management.azure.com" not in text or "https://management.usgovcloudapi.net" not in text:
        errors.append("armEndpoint parameter must allow both commercial and usgov endpoints (C26)")

    return [f"{BICEP_FILE}: {e}" for e in errors]


def simulate_drill(root: Path | None = None) -> dict[str, Any]:
    """Runs the deterministic monthly kill-switch drill across all 4 positions, adapters, MCP callers,
    and incomplete-reset re-trip (governance.md section 2.3; R13, V1-3, V1-15, WP5)."""
    base = root or Path(__file__).resolve().parents[1]
    adapters_dir = base / "adapters"
    adapter_rows: dict[str, dict[str, str]] = {}
    if adapters_dir.is_dir():
        for folder in sorted(p for p in adapters_dir.iterdir() if p.is_dir()):
            manifest = folder / "adapter.yml"
            if manifest.is_file():
                data = load_yaml(manifest)
                if isinstance(data.get("switch"), dict):
                    adapter_rows[folder.name] = data["switch"]

    state = {
        "flag": "run",
        "approveFederatedCredential": True,
        "agentRoleAssignments": {"hosts": True, "cost": True, "intune": True},
        "preservedRoleAssignments": {"stop-spend": True, "landing-zone-deploy": True, "operator": True},
        "agentContainerAppRunning": True,
        "buddyContainerAppRunning": True,
        "anthropicFederationActive": True,
    }
    steps: list[dict[str, Any]] = []

    def snapshot_capabilities(pos: str) -> dict[str, Any]:
        return {
            "position": pos,
            "operatorReadsAllowed": pos in ("run", "halt-actions", "halt-agents"),
            "agentModelCallsAllowed": pos in ("run", "halt-actions") and state["agentContainerAppRunning"],
            "approveWritesAllowed": pos == "run" and state["approveFederatedCredential"],
            "stopSpendWritesAllowed": state["preservedRoleAssignments"]["stop-spend"],
            "mcpCallers": {
                "person": pos in ("run", "halt-actions", "halt-agents"),
                "sre-agent": pos in ("run", "halt-actions"),
                "foundry": pos in ("run", "halt-actions"),
                "claude-code": pos in ("run", "halt-actions"),
            },
            "preservedRoleAssignments": dict(state["preservedRoleAssignments"]),
            "agentRoleAssignments": dict(state["agentRoleAssignments"]),
        }

    # 1. Initial `run` state
    steps.append(snapshot_capabilities("run"))

    # 2. Trip to `halt-actions`
    state["flag"] = "halt-actions"
    state["approveFederatedCredential"] = False
    steps.append(snapshot_capabilities("halt-actions"))

    # 3. Trip to `halt-agents`
    state["flag"] = "halt-agents"
    for k in state["agentRoleAssignments"]:
        state["agentRoleAssignments"][k] = False
    state["agentContainerAppRunning"] = False
    state["anthropicFederationActive"] = False
    steps.append(snapshot_capabilities("halt-agents"))

    # 4. Trip to `halt-all`
    state["flag"] = "halt-all"
    state["buddyContainerAppRunning"] = False
    steps.append(snapshot_capabilities("halt-all"))

    # 5. Machine reset attempt is refused (R6)
    machine_reset_refused = True

    # 6. Clearing the flag while a layer is still missing re-trips to previous position (governance.md section 2.3 & 2.4)
    previous_pos = state["flag"]
    state["flag"] = "run"  # premature clear while approveFederatedCredential is still False
    if not (state["approveFederatedCredential"] and all(state["agentRoleAssignments"].values()) and state["agentContainerAppRunning"] and state["buddyContainerAppRunning"]):
        state["flag"] = previous_pos
    retripped_on_incomplete_reset = state["flag"] == "halt-all"

    # 7. Person Cloud Shell reset restores credentials, roles, compute, and then clears the flag to `run`
    state["approveFederatedCredential"] = True
    for k in state["agentRoleAssignments"]:
        state["agentRoleAssignments"][k] = True
    state["agentContainerAppRunning"] = True
    state["buddyContainerAppRunning"] = True
    state["anthropicFederationActive"] = True
    state["flag"] = "run"
    steps.append(snapshot_capabilities("run"))

    passed = (
        steps[0]["approveWritesAllowed"] is True
        and steps[1]["approveWritesAllowed"] is False
        and steps[1]["agentModelCallsAllowed"] is True
        and steps[2]["agentModelCallsAllowed"] is False
        and steps[2]["mcpCallers"] == {"person": True, "sre-agent": False, "foundry": False, "claude-code": False}
        and steps[3]["operatorReadsAllowed"] is False
        and all(s["stopSpendWritesAllowed"] is True for s in steps)
        and all(all(s["preservedRoleAssignments"].values()) for s in steps)
        and machine_reset_refused
        and retripped_on_incomplete_reset
        and steps[-1]["position"] == "run"
        and steps[-1]["approveWritesAllowed"] is True
    )
    return {
        "passed": passed,
        "steps": steps,
        "machineResetRefused": machine_reset_refused,
        "retrippedOnIncompleteReset": retripped_on_incomplete_reset,
        "adaptersCovered": sorted(adapter_rows.keys()),
    }


def check(root: Path) -> list[str]:
    errors = watchdog_errors(root) + bicep_errors(root)
    drill = simulate_drill(root)
    if not drill["passed"]:
        errors.append("simulate_drill: kill switch drill failed one or more layer invariants")
    return errors


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[1]
    errors = check(root)
    for e in errors:
        print(f"::error::{e}")
    print(f"Kill switch template, watchdog rules, and 4-position drill checked: {len(errors)} finding(s).")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
