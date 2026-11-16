# avd-swarm S1a spike: list the Azure MCP Server's tools. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Q3 evidence: every tool the server exposes, with its read-only and destructive hints.

Runs in Cloud Shell as you, with a delegated token for the MCP server's Entra app (the app
pre-authorizes the Azure CLI). Exits 1 if any tool isn't marked read-only.

    python list_mcp_tools.py --url <CONTAINER_APP_URL> --app-id <MCP app client ID>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def cli_token(scope: str) -> str:
    print(f"Getting a token for {scope} from the Azure CLI...", flush=True)
    az = shutil.which("az") or "az"
    result = subprocess.run(
        [az, "account", "get-access-token", "--scope", scope, "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        sys.exit(f"az account get-access-token failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


async def list_tools(url: str, token: str) -> list[dict]:
    print(f"Listing tools at {url}...", flush=True)
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = []
                cursor = None
                while True:  # page until the server stops returning a cursor (lesson 0008)
                    page = await session.list_tools(cursor=cursor) if cursor else await session.list_tools()
                    for tool in page.tools:
                        hints = tool.annotations
                        tools.append({
                            "name": tool.name,
                            "readOnly": getattr(hints, "readOnlyHint", None),
                            "destructive": getattr(hints, "destructiveHint", None),
                        })
                    cursor = page.nextCursor
                    if not cursor or not page.tools:
                        return tools


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="The MCP endpoint, the Container App URL")
    parser.add_argument("--app-id", required=True, help="Client ID of the MCP server's Entra app")
    args = parser.parse_args()

    token = cli_token(f"api://{args.app_id}/Mcp.Tools.ReadWrite")
    tools = asyncio.run(list_tools(args.url.rstrip("/"), token))
    for tool in tools:
        print(f"{tool['name']:<60} readOnly={tool['readOnly']} destructive={tool['destructive']}")
    print(json.dumps({"step": "list-mcp-tools", "count": len(tools), "tools": tools}))
    not_read_only = [t["name"] for t in tools if t["readOnly"] is not True]
    if not_read_only:
        print(f"RESULT Q3 FAIL: {len(not_read_only)} tool(s) not marked read-only: {', '.join(not_read_only)}")
        sys.exit(1)
    print(f"RESULT Q3 PASS: {len(tools)} tools, all marked read-only")


if __name__ == "__main__":
    main()
