# avd-swarm S1a spike: deploy the hosted agent from code. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Zips spikes/s1a/agent, creates a hosted agent version from it, routes all traffic to it,
and prints the version's agent identity (Q5) as a JSON line for the next step.

    python deploy_agent.py --project-endpoint <url> --resource <foundry account name> \
        --coordinator <deployment> --hosts <deployment> --mcp-url <url> --mcp-app-id <id> \
        --allowed-tools <comma-separated names from list_mcp_tools.py>
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    CodeConfiguration,
    FixedRatioVersionSelectionRule,
    AgentEndpointConfig,
    HostedAgentDefinition,
    ProtocolVersionRecord,
    VersionSelector,
)
from azure.core.exceptions import HttpResponseError
from azure.identity import AzureCliCredential

AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"


def zip_agent(target: Path) -> Path:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("main.py", "requirements.txt"):  # both must sit at the zip root
            archive.write(AGENT_DIR / name, arcname=name)
    return target


def fail(step: str, error: HttpResponseError) -> None:
    """Everything the service returned (lesson 0012), then stop."""
    response = error.response
    print(json.dumps({
        "step": step,
        "status": getattr(response, "status_code", None),
        "code": getattr(error.error, "code", None) if error.error else None,
        "message": str(error.message),
        "body": response.text() if response is not None else None,
        "requestId": response.headers.get("x-ms-request-id") if response is not None else None,
    }))
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--resource", required=True, help="Foundry account name, for the Claude endpoint")
    parser.add_argument("--coordinator", required=True, help="Claude Haiku 5.5 deployment name")
    parser.add_argument("--hosts", required=True, help="Claude Opus 5.5 deployment name")
    parser.add_argument("--mcp-url", required=True)
    parser.add_argument("--mcp-app-id", required=True)
    parser.add_argument("--allowed-tools", required=True)
    parser.add_argument("--agent-name", default="avdswarm-spike")
    parser.add_argument("--runtime", default="python_3_13")
    parser.add_argument("--hosts-effort", default="medium", choices=["low", "medium", "high"])
    args = parser.parse_args()

    project = AIProjectClient(endpoint=args.project_endpoint, credential=AzureCliCredential(), allow_preview=True)
    definition = HostedAgentDefinition(
        cpu="0.5",
        memory="1Gi",
        code_configuration=CodeConfiguration(
            runtime=args.runtime, entry_point=["python", "main.py"], dependency_resolution="remote_build"
        ),
        protocol_versions=[ProtocolVersionRecord(protocol="responses", version="2.0.0")],
        environment_variables={
            "ANTHROPIC_FOUNDRY_RESOURCE": args.resource,
            "COORDINATOR_DEPLOYMENT": args.coordinator,
            "HOSTS_DEPLOYMENT": args.hosts,
            "MCP_URL": args.mcp_url,
            "MCP_SCOPE": f"api://{args.mcp_app_id}/.default",
            "HOSTS_ALLOWED_TOOLS": args.allowed_tools,
            "HOSTS_EFFORT": args.hosts_effort,
            "COORDINATOR_EFFORT": "low",
        },
    )

    with tempfile.TemporaryDirectory() as folder:
        archive = zip_agent(Path(folder) / "agent.zip")
        print(f"Uploading {archive.stat().st_size} bytes and creating a version of {args.agent_name} "
              f"(remote dependency build, {args.runtime})...", flush=True)
        try:
            with archive.open("rb") as code:
                version = project.agents.create_version_from_code(args.agent_name, definition=definition, code=code)
        except HttpResponseError as error:
            fail("create-version", error)

    print(f"Version {version.version} created; waiting for it to become active (up to 10 minutes)...", flush=True)
    for _ in range(60):
        try:
            version = project.agents.get_version(args.agent_name, version.version)
        except HttpResponseError as error:
            fail("get-version", error)
        if version.status in ("active", "failed"):
            break
        time.sleep(10)
    if version.status != "active":
        print(json.dumps({"step": "wait-active", "status": version.status, "version": version.as_dict()}, default=str))
        sys.exit(1)

    print("Routing all traffic to the new version...", flush=True)
    try:
        project.agents.update_details(
            args.agent_name,
            agent_endpoint=AgentEndpointConfig(
                version_selector=VersionSelector(version_selection_rules=[
                    FixedRatioVersionSelectionRule(agent_version=version.version, traffic_percentage=100)
                ])
            ),
        )
    except HttpResponseError as error:
        fail("route-traffic", error)

    identity = version.instance_identity
    print(json.dumps({
        "step": "deploy-agent",
        "agentName": args.agent_name,
        "version": version.version,
        "identityPrincipalId": identity.principal_id if identity else None,
        "identityClientId": identity.client_id if identity else None,
    }))
    if not identity:
        print("RESULT Q5 FAIL: the version has no instance identity")
        sys.exit(1)


if __name__ == "__main__":
    main()
