# avd-swarm S1a spike agent API: hybrid RAG tool layer. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Hybrid RAG tool layer for the S1a agent API (docs/spec.md sections 4 and 4a; avd-second-brain docs/v1-review.md section 4).

Gives the Hosts specialist three deterministic, zero-standing-cost read legs alongside the Azure MCP Server:
  1. `kql_catalog_run`  -- runs a KQL catalog query by ID against the AVD Log Analytics workspace (never raw KQL).
  2. `concierge_search` -- queries the deterministic Concierge (`avd_concierge`) for cited KB, rule, runbook, and app catalog passages.
  3. `concierge_facts`  -- queries the deterministic Concierge for `C11` current-state facts by `C15` (`contracts/fact-register.json`) ID, refusing unknown, `proposed`, or `specified` IDs.

Also implements `verify_citations`, the deterministic code half of the section 4a claim check: extracts every citation token (`c11.*`, `wvd-*`, `knowledge/*`, `docs/*`, `ref:*`, `obs:*`, `gap:*`) from the specialist's answer and checks whether it was returned by a tool call in the same turn.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

import yaml

import runbook_endpoint

LOCAL_RAG_TOOLS = ("kql_catalog_run", "concierge_search", "concierge_facts")

CITATION_PATTERN = re.compile(
    r"\b(?:"
    r"c11\.[A-Za-z0-9_.]+|"
    r"wvd-[a-z0-9-]+|"
    r"(?:ref|obs|gap):[a-z0-9-]+|"
    r"knowledge/(?:catalog/apps\.json#[A-Za-z0-9_.-]+|rules/[A-Za-z0-9_.-]+\.json(?:@v\d+)?|runbooks/[A-Za-z0-9_.-]+\.md)|"
    r"docs/(?:knowledge/[A-Za-z0-9_./-]+\.md|[A-Za-z0-9_.-]+\.md)"
    r")"
)


class RagToolError(ValueError):
    """Raised when a local RAG tool call fails validation before reaching Azure or the Concierge."""


def load_agent_kql_allowlist(root: Path, agent: str = "hosts") -> list[str]:
    """Returns the KQL catalog IDs listed in governance/allowlists/<agent>.yml."""
    path = root / "governance/allowlists" / f"{agent}.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [str(q) for q in (data.get("kql") or [])]


def load_fact_register(root: Path) -> dict[str, dict[str, Any]]:
    """Loads contracts/fact-register.json (C15) indexed by fact ID."""
    reg_path = root / "contracts/fact-register.json"
    data = json.loads(reg_path.read_text(encoding="utf-8"))
    return {f["id"]: f for f in (data.get("facts") or [])}


def built_c11_fact_ids(register: dict[str, dict[str, Any]]) -> list[str]:
    """Returns sorted fact IDs that are built in C11."""
    return sorted(
        fid for fid, spec in register.items()
        if spec.get("status") == "built" and spec.get("contract") == "C11"
    )


def tool_definitions(
    allowed_tools: Iterable[str],
    allowed_kql: Iterable[str],
    register: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Builds Anthropic tool schemas for any local RAG tools enabled in allowed_tools."""
    allowed = set(allowed_tools)
    kql_list = sorted(set(allowed_kql))
    built_ids = built_c11_fact_ids(register) if register else []
    defs: list[dict[str, Any]] = []

    if "kql_catalog_run" in allowed:
        defs.append({
            "name": "kql_catalog_run",
            "description": (
                "Run a vetted KQL query by catalog ID against the AVD Log Analytics workspace "
                "(WVDAgentHealthStatus, WVDErrors, WVDConnections, WVDConnectionNetworkData, WVDCheckpoints). "
                "Never accepts raw KQL strings."
            ),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["queryId"],
                "properties": {
                    "queryId": {
                        "type": "string",
                        "description": f"Catalog query ID from catalog/kql/catalog.yml. Allowed: {', '.join(kql_list)}",
                    }
                },
            },
        })

    if "concierge_search" in allowed:
        defs.append({
            "name": "concierge_search",
            "description": (
                "Search the deterministic AVD Second Brain Concierge for cited passages across "
                "docs/knowledge, sizing/storage/network rules, runbooks, and the application catalog."
            ),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["question"],
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Search terms or question to look up in the knowledge base.",
                    },
                    "top": {
                        "type": "integer",
                        "description": "Maximum number of cited passages to return (1-20, default 5).",
                    },
                },
            },
        })

    if "concierge_facts" in allowed:
        examples = ", ".join(built_ids[:6]) if built_ids else "c11.hostPool.count, c11.sessionHost.count"
        defs.append({
            "name": "concierge_facts",
            "description": (
                "Read built C11 current-state facts by C15 fact-register ID from the Concierge facts store "
                f"(for example: {examples}). Returns only the requested built fact IDs and generatedUtc."
            ),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["ids"],
                "properties": {
                    "ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Built C11 fact IDs from contracts/fact-register.json.",
                    }
                },
            },
        })

    return defs


def validate_kql_request(
    args: Any,
    allowed_kql: Iterable[str],
    catalog_queries: dict[str, str],
) -> tuple[str, str]:
    """Validates a kql_catalog_run tool call and returns (query_id, kql_text)."""
    if not isinstance(args, dict):
        raise RagToolError("kql_catalog_run arguments must be a JSON object")
    extra = sorted(set(args) - {"queryId"})
    if extra:
        raise RagToolError(f"kql_catalog_run accepts only 'queryId', not {extra} (inline KQL is never allowed)")
    query_id = args.get("queryId")
    if not isinstance(query_id, str) or not query_id.strip():
        raise RagToolError("kql_catalog_run requires a non-empty string 'queryId'")
    query_id = query_id.strip()
    if query_id not in catalog_queries:
        raise RagToolError(f"KQL query '{query_id}' is not in catalog/kql/catalog.yml")
    if query_id not in set(allowed_kql):
        raise RagToolError(f"KQL query '{query_id}' is not in this agent's allowlist")
    return query_id, catalog_queries[query_id]


def validate_facts_request(
    args: Any,
    register: dict[str, dict[str, Any]],
) -> list[str]:
    """Validates a concierge_facts tool call against contracts/fact-register.json (C15)."""
    if not isinstance(args, dict):
        raise RagToolError("concierge_facts arguments must be a JSON object")
    extra = sorted(set(args) - {"ids"})
    if extra:
        raise RagToolError(f"concierge_facts accepts only 'ids', not {extra}")
    raw_ids = args.get("ids")
    if not isinstance(raw_ids, list) or not raw_ids or not all(isinstance(i, str) and i.strip() for i in raw_ids):
        raise RagToolError("concierge_facts requires a non-empty list of fact IDs")

    clean_ids: list[str] = []
    unknown: list[str] = []
    unbuilt: list[str] = []
    for raw in raw_ids:
        fid = raw.strip()
        if fid not in clean_ids:
            clean_ids.append(fid)
        spec = register.get(fid)
        if spec is None:
            unknown.append(fid)
        elif spec.get("status") != "built" or spec.get("contract") != "C11":
            unbuilt.append(f"{fid} (status={spec.get('status')}, contract={spec.get('contract')})")

    if unknown:
        raise RagToolError(f"Unknown fact ID(s) not in C15 fact-register.json: {', '.join(unknown)}")
    if unbuilt:
        raise RagToolError(f"Fact ID(s) are not built in C11: {', '.join(unbuilt)}")
    return clean_ids


def validate_search_request(args: Any) -> tuple[str, int]:
    """Validates a concierge_search tool call and returns (question, top)."""
    if not isinstance(args, dict):
        raise RagToolError("concierge_search arguments must be a JSON object")
    extra = sorted(set(args) - {"question", "top"})
    if extra:
        raise RagToolError(f"concierge_search accepts only 'question' and 'top', not {extra}")
    question = args.get("question")
    if not isinstance(question, str) or not question.strip():
        raise RagToolError("concierge_search requires a non-empty string 'question'")
    top = args.get("top", 5)
    if isinstance(top, bool) or not isinstance(top, int) or top < 1 or top > 20:
        raise RagToolError("concierge_search 'top' must be an integer between 1 and 20")
    return question.strip(), top


def extract_citations(text: str) -> list[str]:
    """Extracts structured citation tokens from an agent's answer text."""
    seen: list[str] = []
    for match in CITATION_PATTERN.finditer(text or ""):
        token = match.group(0).rstrip(".,;:)")
        if token not in seen:
            seen.append(token)
    return seen


def verify_citations(text: str, observed_refs: Iterable[str]) -> dict[str, Any]:
    """Checks every citation token in `text` against the references returned by tools in this turn (spec section 4a)."""
    cited = extract_citations(text)
    pool = set(observed_refs)
    verified: list[str] = []
    unverified: list[str] = []
    for token in cited:
        # Match exact ref or versionless rule prefix if the pool has @v<n>
        if token in pool or any(p.startswith(f"{token}@v") for p in pool):
            verified.append(token)
        else:
            unverified.append(token)
    return {
        "cited": cited,
        "verified": verified,
        "unverified": unverified,
        "allVerified": len(unverified) == 0,
    }


@dataclass
class RagRuntime:
    """Executes the 3 local RAG tools for one specialist turn and records every returned reference."""

    root: Path
    agent: str = "hosts"
    query_logs: Callable[[str], Awaitable[dict[str, Any]]] | None = None
    concierge_search: Callable[[str, int], Awaitable[dict[str, Any]]] | None = None
    concierge_facts: Callable[[list[str]], Awaitable[dict[str, Any]]] | None = None
    allowed_kql: list[str] = field(init=False)
    catalog_queries: dict[str, str] = field(init=False)
    register: dict[str, dict[str, Any]] = field(init=False)
    observed_refs: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.allowed_kql = load_agent_kql_allowlist(self.root, self.agent)
        self.catalog_queries = runbook_endpoint.catalog_queries(self.root)
        self.register = load_fact_register(self.root)

    def tools_for(self, allowed_tools: Iterable[str]) -> list[dict[str, Any]]:
        return tool_definitions(allowed_tools, self.allowed_kql, self.register)

    async def call(self, name: str, args: Any) -> tuple[str, bool]:
        """Executes a local RAG tool call and returns (json_text, is_error)."""
        try:
            if name == "kql_catalog_run":
                query_id, kql = validate_kql_request(args, self.allowed_kql, self.catalog_queries)
                if self.query_logs is None:
                    raise RagToolError("LOG_WORKSPACE_ID or RUNBOOK_IDENTITY_APP_ID is not configured for kql_catalog_run")
                raw = await self.query_logs(kql)
                rows = runbook_endpoint.rows_from_log_analytics(raw)
                self.observed_refs.add(query_id)
                return json.dumps({"queryId": query_id, "rowCount": len(rows), "rows": rows}), False

            if name == "concierge_search":
                question, top = validate_search_request(args)
                if self.concierge_search is None:
                    raise RagToolError("Concierge search is not configured (set CONCIERGE_URL or CONCIERGE_BRAIN_ROOT)")
                payload = await self.concierge_search(question, top)
                for hit in payload.get("hits") or []:
                    ref = hit.get("ref")
                    if isinstance(ref, str) and ref:
                        self.observed_refs.add(ref)
                    for inline in extract_citations(f"{hit.get('title', '')} {hit.get('snippet', '')}"):
                        self.observed_refs.add(inline)
                return json.dumps(payload), False

            if name == "concierge_facts":
                fact_ids = validate_facts_request(args, self.register)
                if self.concierge_facts is None:
                    raise RagToolError("Concierge facts store is not configured (set CONCIERGE_URL or CONCIERGE_FACTS_SNAPSHOT)")
                payload = await self.concierge_facts(fact_ids)
                for fid in (payload.get("facts") or {}):
                    self.observed_refs.add(fid)
                return json.dumps(payload), False

            raise RagToolError(f"Unknown local RAG tool: {name}")
        except Exception as error:
            return json.dumps({"error": str(error), "tool": name}), True
