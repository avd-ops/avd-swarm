#!/usr/bin/env python3
"""Builds and validates read-only, zero-secret MCP client configurations (.mcp.json) for avd-swarm.

Deployed avd-swarm agents and runbooks use Microsoft's Azure MCP Server (microsoft/mcp) in
`--mode all --read-only` with an explicit `--namespace` list so each agent's per-tool allowlist
can enforce exact tool schemas and cache them with ephemeral prompt caching.

For interactive operator troubleshooting in Cloud Shell or an IDE (Claude Code, Gemini CLI, VS Code),
operators can optionally add Microsoft's Azure CLI MCP Server (Azure/azure-api-mcp) locked to
`--readonly` via `--include-az-cli-mcp`.

Deterministic collectors, validators, Bicep deployments and spike test harnesses (Invoke-Spike.ps1)
never use MCP so they stay deterministic and cost $0 in LLM tokens.

Personal project, not for production, as is; not affiliated with the author's employer,
Liquidware or Microsoft.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

ALLOWED_NAMESPACES: tuple[str, ...] = (
    "virtualdesktop",
    "monitor",
    "kusto",
    "advisor",
    "pricing",
    "resourcehealth",
    "policy",
    "role",
    "quota",
)

DEFAULT_NAMESPACES: tuple[str, ...] = ("virtualdesktop",)

FORBIDDEN_ENV_KEY = re.compile(
    r"(SECRET|PASSWORD|TOKEN|API_KEY|APIKEY|PRIVATE_KEY|CLIENT_SECRET|CONNECTION_STRING)",
    re.IGNORECASE,
)
GUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


def build_mcp_config(
    namespaces: Sequence[str] = DEFAULT_NAMESPACES,
    include_az_cli_mcp: bool = False,
) -> dict[str, Any]:
    """Return a zero-secret, read-only .mcp.json dictionary."""
    cleaned = [ns.strip().lower() for ns in namespaces if ns and ns.strip()]
    if not cleaned:
        raise ValueError("at least one Azure MCP namespace is required")
    unknown = [ns for ns in cleaned if ns not in ALLOWED_NAMESPACES]
    if unknown:
        raise ValueError(
            f"disallowed namespace(s): {', '.join(unknown)} "
            f"(allowed: {', '.join(ALLOWED_NAMESPACES)})"
        )
    deduped = list(dict.fromkeys(cleaned))

    azure_args = ["-y", "@azure/mcp@latest", "server", "start", "--mode", "all", "--read-only"]
    for ns in deduped:
        azure_args.extend(["--namespace", ns])

    servers: dict[str, Any] = {
        "azure-mcp": {
            "command": "npx",
            "args": azure_args,
        }
    }
    if include_az_cli_mcp:
        servers["az-cli-mcp"] = {
            "command": "npx",
            "args": ["-y", "@azure/Lists-or-az-api-mcp", "--readonly"]
            if False
            else ["az-api-mcp", "--readonly"],
        }

    config = {"mcpServers": servers}
    problems = validate_mcp_config(config)
    if problems:
        raise ValueError("; ".join(problems))
    return config


def validate_mcp_config(config: dict[str, Any]) -> list[str]:
    """Validate that an MCP configuration is strictly read-only and contains no secrets or IDs."""
    problems: list[str] = []
    if not isinstance(config, dict):
        return ["config must be a JSON object"]
    servers = config.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        return ["mcpServers must be a non-empty object"]

    for name, spec in servers.items():
        if not isinstance(spec, dict):
            problems.append(f"{name}: server entry must be an object")
            continue
        args = spec.get("args")
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            problems.append(f"{name}: args must be a list of strings")
            continue

        if "--read-only" not in args and "--readonly" not in args:
            problems.append(f"{name}: missing mandatory read-only flag (--read-only or --readonly)")

        if name == "azure-mcp":
            if "--read-only" not in args:
                problems.append("azure-mcp: must include --read-only")
            if "--mode" not in args or "all" not in args:
                problems.append("azure-mcp: must include --mode all for per-tool allowlisting")
            if "--namespace" not in args:
                problems.append("azure-mcp: must scope namespaces explicitly with --namespace")
            for idx, token in enumerate(args):
                if token == "--namespace" and idx + 1 < len(args):
                    ns = args[idx + 1]
                    if ns not in ALLOWED_NAMESPACES:
                        problems.append(f"azure-mcp: namespace {ns!r} is not in ALLOWED_NAMESPACES")

        if name == "az-cli-mcp" and "--readonly" not in args:
            problems.append("az-cli-mcp: must include --readonly")

        for arg in args:
            if GUID_PATTERN.search(arg):
                problems.append(f"{name}: args must not contain tenant/subscription/client GUIDs")
            if FORBIDDEN_ENV_KEY.search(arg):
                problems.append(f"{name}: args must not contain secrets or keys")

        env = spec.get("env")
        if env is not None:
            if not isinstance(env, dict):
                problems.append(f"{name}: env must be an object when present")
            else:
                for k, v in env.items():
                    if FORBIDDEN_ENV_KEY.search(str(k)) or FORBIDDEN_ENV_KEY.search(str(v)):
                        problems.append(f"{name}: env key {k!r} looks like a secret or credential")
                    if GUID_PATTERN.search(str(v)):
                        problems.append(f"{name}: env key {k!r} contains a GUID")

    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate or validate a read-only, zero-secret .mcp.json for AVD operations."
    )
    parser.add_argument(
        "--namespace",
        dest="namespaces",
        action="append",
        help="Azure MCP namespace to expose (repeatable; default: virtualdesktop).",
    )
    parser.add_argument(
        "--all-avd-namespaces",
        action="store_true",
        help="Expose all allowlisted read-only AVD/ops namespaces.",
    )
    parser.add_argument(
        "--include-az-cli-mcp",
        action="store_true",
        help="Include the optional Azure/azure-api-mcp server with --readonly for interactive CLI troubleshooting.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the generated JSON to this file path instead of stdout.",
    )
    parser.add_argument(
        "--check",
        type=Path,
        help="Validate an existing .mcp.json file and exit non-zero on policy violations.",
    )
    args = parser.parse_args(argv)

    if args.check:
        try:
            doc = json.loads(args.check.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"RESULT FAIL ({args.check}: {exc})", file=sys.stderr)
            return 1
        problems = validate_mcp_config(doc)
        if problems:
            for p in problems:
                print(p, file=sys.stderr)
            print(f"RESULT FAIL ({len(problems)} problem(s))", file=sys.stderr)
            return 1
        print(f"RESULT PASS ({args.check})")
        return 0

    if args.all_avd_namespaces:
        namespaces: Sequence[str] = ALLOWED_NAMESPACES
    elif args.namespaces:
        namespaces = args.namespaces
    else:
        namespaces = DEFAULT_NAMESPACES

    try:
        config = build_mcp_config(
            namespaces=namespaces,
            include_az_cli_mcp=args.include_az_cli_mcp,
        )
    except ValueError as exc:
        print(f"RESULT FAIL ({exc})", file=sys.stderr)
        return 1

    text = json.dumps(config, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
