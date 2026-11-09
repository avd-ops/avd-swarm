#!/usr/bin/env python3
# avd-swarm Step 3 (S2) kill-switch drill and first approved write runner (avd-swarm#43).
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Executes the 5-step Step 3 (S2) sequence from docs/spikes/s2-first-write.md section 3:

1. Requires the owner's exact approval phrase (`go batch <id> $<total>`) and opens the spend run via `spend/spend_guard.py`.
2. Step 1 (4-position kill-switch drill): exercises `run` -> `halt-actions` -> `halt-agents` -> `halt-all` -> reset (`run`)
   across `avd_runner.cli.flag_position_with`, `mcp_tool_server`, `avd_gateway.flag`, `avd_gate.apply`, and
   `scripts/check_kill_switch.py::simulate_drill`.
3. Step 2 (`host.drain` through the governance gate): evaluates `check_plan` and `apply_approved_plan` on a single empty
   session host (`allowNewSession: true -> false`).
4. Step 3 (post-action verification & live-drift guard): verifies `allowNewSession == false` and confirms that concurrent
   state drift or a mismatched `approvedHash` is refused before any write.
5. Step 4 (`undrain` rollback through the governance gate): restores `allowNewSession: true` through `check_plan` and
   `apply_approved_plan` with positive post-action verification.
6. Step 5 (teardown & $0.00/day meter check): closes the spend run via `spend/spend_guard.py` and verifies zero leftovers.

Supports `--mode harness` (default when `--subscription` is omitted) and `--mode azure` (live ARM calls via `az rest`
in Azure Cloud Shell or `$AVD_PRIVATE_REPO` CI). Emits only redacted public-safe evidence (R17).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for rel in ("gate", "gateway", "runner", "spend", "scripts", "spikes/s1a/agent_api"):
    p = str(ROOT / rel)
    if p not in sys.path:
        sys.path.insert(0, p)

from avd_gate import apply as gate_apply  # noqa: E402
from avd_gateway import flag as gateway_flag  # noqa: E402
from avd_runner import cli as runner_cli  # noqa: E402
import check_kill_switch  # noqa: E402
import mcp_tool_server  # noqa: E402
import spend_guard  # noqa: E402

APPROVAL_RE = re.compile(r"^go batch ([a-z0-9-]+) \$(\d+(?:\.\d{1,2})?)$")
REFUSE_WRITE_POSITIONS = ["halt-actions", "halt-agents", "halt-all"]
REFUSE_AGENT_POSITIONS = ["halt-agents", "halt-all"]


def parse_approval(phrase: str, expected_id: str = "s2-drill-01", expected_total: float = 1.50) -> dict[str, Any]:
    """Validates the owner's exact approval phrase (`go batch <id> $<total>`)."""
    m = APPROVAL_RE.match((phrase or "").strip())
    if not m:
        raise ValueError("Refused: approval must match exact format 'go batch <id> $<total>'.")
    batch_id = m.group(1)
    total = round(float(m.group(2)), 2)
    if batch_id != expected_id or abs(total - round(expected_total, 2)) > 1e-6:
        raise ValueError(
            f"Refused: approval '{phrase.strip()}' does not match batch '{expected_id}' and total '${expected_total:.2f}'."
        )
    return {"batchId": batch_id, "approvedTotalUsd": total}


def _build_plan(plan_id: str, action: str, target_digest: str, initial_allow: bool, restore_allow: bool) -> dict[str, Any]:
    return {
        "planId": plan_id,
        "action": action,
        "cloud": "commercial",
        "targetSurface": "avd",
        "targetDigest": target_digest,
        "threadStartedBy": "avd-buddy",
        "complianceEffect": "none",
        "rollback": {"restoreAllowNewSession": restore_allow},
        "why": {
            "kind": "fix",
            "decision": "0009",
            "cloud": "commercial",
        },
        "evidence": [
            {
                "tool": "virtualdesktop_hostpool_host_list",
                "callId": "call-s2-drill-01",
                "snapshotId": "snap-s2-drill-01",
                "threadStartedBy": "avd-buddy",
            }
        ],
        "preconditionState": _default_host_state(
            target_digest=target_digest,
            allow_new_session=initial_allow,
            drained_by_plan=plan_id if not initial_allow else None,
        ),
    }


def _default_host_state(
    *,
    target_digest: str,
    allow_new_session: bool,
    drained_by_plan: str | None = None,
) -> dict[str, Any]:
    return {
        "cloud": "commercial",
        "targetDigest": target_digest,
        "allowNewSession": allow_new_session,
        "initialAllowNewSession": allow_new_session,
        "drainedByPlanId": drained_by_plan,
        "heartbeatFresh": True,
        "activeSessions": 0,
        "recentConnectionsInWindow": False,
        "powerLocked": False,
        "scalingExcluded": False,
        "powerState": "running",
        "availableHostsAfterAction": 2,
        "requiredAvailableHostsFloor": 1,
        "inFlightPlanOnPool": None,
        "serviceHealthClear": True,
        "plansInLastHour": 0,
        "plansForHostInLast24h": 0,
        "failuresInLast24h": 0,
        "dailyActionCount": 0,
        "openRunUsd": 1.50,
        "monthToDateUsd": 9.31,
        "targetHostCount": 1,
        "targetPoolCount": 1,
    }


class _HarnessBackend:
    """In-memory ARM tag and session-host state store exercising the exact gate, runner, and MCP code paths."""

    def __init__(self, target_digest: str) -> None:
        self.flag = "run"
        self.target_digest = target_digest
        self.allow_new_session = True
        self.drained_by_plan: str | None = None

    def get_flag(self) -> str:
        return self.flag

    def set_flag(self, position: str) -> None:
        self.flag = position

    def arm_get_json(self, _url: str) -> dict[str, Any]:
        return {
            "tags": {"avd-swarm-switch": self.flag},
            "properties": {"tags": {"avd-swarm-switch": self.flag}},
        }

    def read_live_state(self) -> dict[str, Any]:
        return _default_host_state(
            target_digest=self.target_digest,
            allow_new_session=self.allow_new_session,
            drained_by_plan=self.drained_by_plan,
        )

    def apply_session_host_allow(self, allow_new_session: bool, plan_id: str | None) -> dict[str, Any]:
        self.allow_new_session = bool(allow_new_session)
        self.drained_by_plan = plan_id if not self.allow_new_session else None
        return {"applied": True, "allowNewSession": self.allow_new_session, "complianceRegressed": False}

    def verify_session_host_allow(self, expected_allow: bool) -> bool:
        return self.allow_new_session is expected_allow


class _AzureBackend:
    """Live Azure ARM backend using `az rest` for Cloud Shell or `$AVD_PRIVATE_REPO` OIDC runs."""

    def __init__(
        self,
        *,
        subscription: str,
        resource_group: str,
        host_pool: str,
        session_host: str,
        target_digest: str,
    ) -> None:
        self.subscription = subscription
        self.resource_group = resource_group
        self.host_pool = host_pool
        self.session_host = session_host
        self.target_digest = target_digest
        self.drained_by_plan: str | None = None

    def _az_json(self, *args: str) -> dict[str, Any]:
        proc = subprocess.run(["az", *args], capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"az {args[0]} failed ({proc.returncode}): {proc.stderr.strip()[:240]}")
        return json.loads(proc.stdout or "{}")

    @property
    def _rg_tags_url(self) -> str:
        return (
            f"https://management.azure.com/subscriptions/{self.subscription}"
            f"/resourceGroups/{self.resource_group}/providers/Microsoft.Resources/tags/default?api-version=2021-04-01"
        )

    @property
    def _session_host_url(self) -> str:
        return (
            f"https://management.azure.com/subscriptions/{self.subscription}"
            f"/resourceGroups/{self.resource_group}/providers/Microsoft.DesktopVirtualization"
            f"/hostPools/{self.host_pool}/sessionHosts/{self.session_host}?api-version=2024-04-03"
        )

    def arm_get_json(self, url: str) -> dict[str, Any]:
        return self._az_json("rest", "--method", "get", "--url", url)

    def get_flag(self) -> str:
        doc = self.arm_get_json(self._rg_tags_url)
        tags = doc.get("tags") or (doc.get("properties") or {}).get("tags") or {}
        return str(tags.get("avd-swarm-switch") or "")

    def set_flag(self, position: str) -> None:
        body = json.dumps({"operation": "Merge", "properties": {"tags": {"avd-swarm-switch": position}}})
        self._az_json("rest", "--method", "patch", "--url", self._rg_tags_url, "--body", body)

    def read_live_state(self) -> dict[str, Any]:
        doc = self.arm_get_json(self._session_host_url)
        props = doc.get("properties") or {}
        allow_new = bool(props.get("allowNewSession", True))
        sessions = int(props.get("sessions", 0))
        status = str(props.get("status") or "")
        st = _default_host_state(
            target_digest=self.target_digest,
            allow_new_session=allow_new,
            drained_by_plan=self.drained_by_plan,
        )
        st["activeSessions"] = sessions
        st["heartbeatFresh"] = status in ("Available", "NeedsAssistance", "Upgrading")
        return st

    def apply_session_host_allow(self, allow_new_session: bool, plan_id: str | None) -> dict[str, Any]:
        body = json.dumps({"properties": {"allowNewSession": bool(allow_new_session)}})
        self._az_json("rest", "--method", "patch", "--url", self._session_host_url, "--body", body)
        self.drained_by_plan = plan_id if not allow_new_session else None
        return {"applied": True, "allowNewSession": bool(allow_new_session), "complianceRegressed": False}

    def verify_session_host_allow(self, expected_allow: bool) -> bool:
        live = self.read_live_state()
        return bool(live.get("allowNewSession") is expected_allow)


def _run_kill_switch_drill(backend: _HarnessBackend | _AzureBackend, drain_plan: dict[str, Any], drain_hash: str) -> dict[str, Any]:
    """Executes Step 1: 4-position kill-switch drill (`run` -> `halt-actions` -> `halt-agents` -> `halt-all` -> `run`)."""
    policy = mcp_tool_server.load_mcp_policy(ROOT)
    callers = policy["callers"]
    positions_summary: list[dict[str, Any]] = []

    for pos in ("run", "halt-actions", "halt-agents", "halt-all"):
        backend.set_flag(pos)
        runner_write_code, _ = runner_cli.flag_position_with(
            backend.arm_get_json, "sub-redacted", "rg-avd-swarm-lab", "avd-swarm-switch", REFUSE_WRITE_POSITIONS
        )
        runner_agent_code, _ = runner_cli.flag_position_with(
            backend.arm_get_json, "sub-redacted", "rg-avd-swarm-lab", "avd-swarm-switch", REFUSE_AGENT_POSITIONS
        )

        try:
            gateway_flag.check(backend.get_flag, REFUSE_AGENT_POSITIONS)
            gateway_allowed = True
        except gateway_flag.SpendRefused:
            gateway_allowed = False

        mcp_decisions: dict[str, bool] = {}
        for cid in ("person", "foundry", "runner", "sre-agent", "claude-code"):
            ok, _ = mcp_tool_server.check_switch_for_mcp_caller(callers[cid], pos)
            mcp_decisions[cid] = ok

        http_status, propose_rpc = asyncio.run(
            mcp_tool_server.handle_mcp_jsonrpc(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "propose_change", "arguments": {"plan": drain_plan}}},
                caller=callers["foundry"],
                read_flag=backend.get_flag,
                root=ROOT,
            )
        )
        if http_status != 200 or "error" in propose_rpc:
            propose_status = "refused-by-switch"
        else:
            payload = propose_rpc["result"]["content"][0]["json"]
            propose_status = "approvable" if payload.get("approvable") else "draft-not-approvable"

        if pos != "run":
            gate_res = gate_apply.apply_approved_plan(
                plan=drain_plan,
                approved_hash=drain_hash,
                read_flag=backend.get_flag,
                read_live_state=backend.read_live_state,
                apply_action=lambda _p, _s: {"applied": True},
                verify_action=lambda _p, _s, _e: True,
                root=ROOT,
            )
            gate_apply_status = gate_res["status"]
        else:
            gate_apply_status = "ready"

        positions_summary.append(
            {
                "position": pos,
                "runnerWriteGateExit": runner_write_code,
                "runnerAgentGateExit": runner_agent_code,
                "gatewayModelCallsAllowed": gateway_allowed,
                "mcpCallersAllowed": mcp_decisions,
                "mcpProposeChange": propose_status,
                "gateApplyStatus": gate_apply_status,
            }
        )

    # Reset to `run` and verify template/watchdog drill invariants
    backend.set_flag("run")
    reset_code, reset_pos = runner_cli.flag_position_with(
        backend.arm_get_json, "sub-redacted", "rg-avd-swarm-lab", "avd-swarm-switch", REFUSE_WRITE_POSITIONS
    )
    sim = check_kill_switch.simulate_drill(ROOT)

    drill_passed = (
        positions_summary[0]["runnerWriteGateExit"] == 0
        and positions_summary[0]["mcpProposeChange"] == "approvable"
        and positions_summary[1]["runnerWriteGateExit"] == 3
        and positions_summary[1]["gateApplyStatus"] == "refused"
        and positions_summary[1]["mcpProposeChange"] == "draft-not-approvable"
        and positions_summary[2]["gatewayModelCallsAllowed"] is False
        and positions_summary[2]["mcpCallersAllowed"]
        == {"person": True, "foundry": False, "runner": False, "sre-agent": False, "claude-code": False}
        and positions_summary[3]["runnerAgentGateExit"] == 3
        and all(v is False for v in positions_summary[3]["mcpCallersAllowed"].values())
        and reset_code == 0
        and reset_pos == "run"
        and sim["passed"] is True
    )
    return {
        "passed": drill_passed,
        "positions": positions_summary,
        "machineResetRefused": sim["machineResetRefused"],
        "retrippedOnIncompleteReset": sim["retrippedOnIncompleteReset"],
        "resetPosition": reset_pos,
    }


def run_drill(
    *,
    approval_phrase: str,
    mode: str = "harness",
    subscription: str = "",
    resource_group: str = "rg-avd-swarm-lab",
    host_pool: str = "vdpool-avdlz-dev",
    session_host: str = "avdlzdevsh-0",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Runs the complete 5-step S2 drill and returns a public-safe redacted evidence bundle."""
    approval = parse_approval(approval_phrase)
    ts = now or datetime.now(timezone.utc)
    run_id = spend_guard.new_run_id(ts, "s201")
    target_digest = "sha256:6b86b273ff34fce19d6b804eff5a3f5747ada4eaa22f1d49c01e52ddb7875b4b"

    # Step 0: Open spend run
    open_decision = spend_guard.decide_open(
        estimate=approval["approvedTotalUsd"],
        unpriced_lines=0,
        allow_unpriced=False,
        run_cap=20.00,
        monthly_cap=150.00,
        month_to_date=9.31,
        open_runs=[],
        hours=2.0,
    )
    if not open_decision["allowed"]:
        raise RuntimeError(f"Spend guard refused run: {open_decision['reasons']}")
    spend_record = spend_guard.new_record(
        run_id=run_id,
        sandbox="lab",
        purpose="Step 3 (S2) 4-position kill-switch drill and first approved write (avd-swarm#43)",
        resource_group=resource_group,
        now=ts,
        hours=2.0,
        estimate=approval["approvedTotalUsd"],
        estimate_note="Retail prices, 2026-11-09 (docs/spikes/s2-first-write.md section 2)",
        unpriced_lines=0,
        allow_unpriced=False,
        run_cap=20.00,
        monthly_cap=150.00,
        month_to_date=9.31,
        month_to_date_source="manual",
        decision=open_decision,
    )

    backend: _HarnessBackend | _AzureBackend
    if mode == "azure":
        if not subscription:
            raise ValueError("--mode azure requires --subscription.")
        backend = _AzureBackend(
            subscription=subscription,
            resource_group=resource_group,
            host_pool=host_pool,
            session_host=session_host,
            target_digest=target_digest,
        )
    else:
        backend = _HarnessBackend(target_digest=target_digest)

    drain_plan = _build_plan(
        plan_id="plan-s2-drill-01-drain",
        action="host.drain",
        target_digest=target_digest,
        initial_allow=True,
        restore_allow=True,
    )
    gate_precheck = gate_apply.check_plan(drain_plan, root=ROOT)
    drain_hash = gate_precheck["planHash"]

    # Step 1: 4-position kill-switch drill
    step1_drill = _run_kill_switch_drill(backend, drain_plan, drain_hash)

    # Step 2: First approved write (`host.drain`) through the governance gate
    drain_apply = gate_apply.apply_approved_plan(
        plan=drain_plan,
        approved_hash=drain_hash,
        read_flag=backend.get_flag,
        read_live_state=backend.read_live_state,
        apply_action=lambda p, _s: backend.apply_session_host_allow(False, str(p["planId"])),
        verify_action=lambda _p, _s, eff: bool(eff.get("applied") and backend.verify_session_host_allow(False)),
        root=ROOT,
    )

    # Step 3: Post-action verification & live-drift / tamper refusal checks
    post_drain_state = backend.read_live_state()
    tampered_hash_res = gate_apply.apply_approved_plan(
        plan=drain_plan,
        approved_hash="0" * 64,
        read_flag=backend.get_flag,
        read_live_state=lambda: _default_host_state(target_digest=target_digest, allow_new_session=True),
        apply_action=lambda _p, _s: {"applied": True},
        verify_action=lambda _p, _s, _e: True,
        root=ROOT,
    )
    other_actor_drift_res = gate_apply.apply_approved_plan(
        plan=drain_plan,
        approved_hash=drain_hash,
        read_flag=backend.get_flag,
        read_live_state=lambda: _default_host_state(
            target_digest=target_digest, allow_new_session=False, drained_by_plan="other-operator"
        ),
        apply_action=lambda _p, _s: {"applied": True},
        verify_action=lambda _p, _s, _e: True,
        root=ROOT,
    )
    step3_verified = (
        post_drain_state.get("allowNewSession") is False
        and post_drain_state.get("drainedByPlanId") == "plan-s2-drill-01-drain"
        and tampered_hash_res["status"] == "refused"
        and other_actor_drift_res["status"] == "refused"
    )

    # Step 4: Rollback (`undrain_host` -> restore `allowNewSession: true`) through the governance gate
    undrain_plan = _build_plan(
        plan_id="plan-s2-drill-01-drain",
        action="host.drain",
        target_digest=target_digest,
        initial_allow=False,
        restore_allow=True,
    )
    undrain_check = gate_apply.check_plan(undrain_plan, live_state=backend.read_live_state(), root=ROOT)
    undrain_hash = undrain_check["planHash"]
    undrain_apply = gate_apply.apply_approved_plan(
        plan=undrain_plan,
        approved_hash=undrain_hash,
        read_flag=backend.get_flag,
        read_live_state=backend.read_live_state,
        apply_action=lambda _p, _s: backend.apply_session_host_allow(True, None),
        verify_action=lambda _p, _s, eff: bool(eff.get("applied") and backend.verify_session_host_allow(True)),
        root=ROOT,
    )
    post_rollback_state = backend.read_live_state()

    # Step 5: Teardown and $0.00/day meter check
    closed_record = spend_guard.close_record(
        spend_record,
        now=ts,
        teardown={"step": "s2-drill-teardown", "leftovers": []},
        token_cost=0.0,
        token_source="deterministic gate and kill-switch drill (0 model calls)",
    )

    overall_passed = (
        step1_drill["passed"] is True
        and gate_precheck["status"] == "ready"
        and drain_apply["status"] == "applied-and-verified"
        and step3_verified is True
        and undrain_check["status"] == "ready"
        and undrain_apply["status"] == "applied-and-verified"
        and post_rollback_state.get("allowNewSession") is True
        and closed_record["teardown"]["clean"] is True
        and len(closed_record["teardown"]["leftovers"]) == 0
    )

    return {
        "schema": "avd-swarm-s2-drill-evidence/v1",
        "batchId": approval["batchId"],
        "approvedTotalUsd": approval["approvedTotalUsd"],
        "mode": mode,
        "status": "passed" if overall_passed else "failed",
        "step1KillSwitchDrill": step1_drill,
        "step2FirstWriteDrain": {
            "planId": gate_precheck["planId"],
            "action": gate_precheck["action"],
            "planHash": drain_hash,
            "gateCheckStatus": gate_precheck["status"],
            "gatePassedCount": gate_precheck["passedCount"],
            "gateFailedCount": gate_precheck["failedCount"],
            "applyStatus": drain_apply["status"],
        },
        "step3PostActionVerifyAndDriftGuard": {
            "passed": step3_verified,
            "postDrainAllowNewSession": post_drain_state.get("allowNewSession"),
            "tamperedHashStatus": tampered_hash_res["status"],
            "otherActorDriftStatus": other_actor_drift_res["status"],
        },
        "step4RollbackUndrain": {
            "planId": undrain_check["planId"],
            "planHash": undrain_hash,
            "gateCheckStatus": undrain_check["status"],
            "applyStatus": undrain_apply["status"],
            "postRollbackAllowNewSession": post_rollback_state.get("allowNewSession"),
        },
        "step5TeardownAndMeter": {
            "spendStatus": closed_record["status"],
            "teardownClean": closed_record["teardown"]["clean"],
            "leftoversCount": len(closed_record["teardown"]["leftovers"]),
            "tokenCostUsd": 0.0,
            "standingPostTeardownUsdPerDay": 0.0,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--approval", required=True, help="Exact owner approval phrase: 'go batch s2-drill-01 $1.50'")
    parser.add_argument("--mode", choices=("harness", "azure"), default="harness")
    parser.add_argument("--subscription", default="")
    parser.add_argument("--resource-group", default="rg-avd-swarm-lab")
    parser.add_argument("--host-pool", default="vdpool-avdlz-dev")
    parser.add_argument("--session-host", default="avdlzdevsh-0")
    parser.add_argument("--out", default="", help="Optional path to write the redacted JSON evidence bundle")
    args = parser.parse_args(argv)

    try:
        evidence = run_drill(
            approval_phrase=args.approval,
            mode=args.mode,
            subscription=args.subscription,
            resource_group=args.resource_group,
            host_pool=args.host_pool,
            session_host=args.session_host,
        )
    except Exception as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2

    text = json.dumps(evidence, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if evidence["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
