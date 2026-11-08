# avd-swarm S1a spike agent API: offline 3-leg RAG and citation evaluation harness. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Offline evaluation harness for the 3-leg hybrid RAG tool layer and deterministic citation check
(docs/spec.md sections 4 and 4a; evals/rag/cases.yml).

Exercises:
  1. `kql_catalog_run` over fixture `WVD*` Log Analytics tables, enforcing catalog and per-agent allowlist checks.
  2. `concierge_search` over the fixture KB/rules/apps corpus, with optional Prompt Shields scanning (`observe` / `enforce`).
  3. `concierge_facts` over a C15-gated (`contracts/fact-register.json`) `avd-current-state-facts/v1` (`C11`) snapshot.
  4. `verify_citations` (spec section 4a) partitioning verified vs. hallucinated/withheld citations.

Stdlib + PyYAML only, so CI runs the full suite offline with zero cloud or token spend.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from avd_gateway.shield import Shield
import rag_tools

WORD_RE = re.compile(r"[a-z0-9]{2,}")


def extract_pointer(document: Any, pointer: str) -> list[Any]:
    """Extracts values matching a C15 JSON pointer (with * for array elements)."""
    segments = [s for s in pointer.strip("/").split("/") if s]
    nodes: list[Any] = [document]
    for seg in segments:
        next_nodes: list[Any] = []
        for node in nodes:
            if seg == "*":
                if isinstance(node, list):
                    next_nodes.extend(node)
            elif isinstance(node, dict) and seg in node and node[seg] is not None:
                next_nodes.append(node[seg])
        nodes = next_nodes
        if not nodes:
            return []
    return nodes


def resolve_c11_fact(snapshot: dict[str, Any], spec: dict[str, Any]) -> Any:
    """Resolves a single built C11 fact from a snapshot according to its C15 register entry."""
    values = extract_pointer(snapshot, spec.get("path") or "")
    aggregate = spec.get("aggregate")
    if aggregate == "count":
        total = 0
        for val in values:
            if isinstance(val, (list, dict)):
                total += len(val)
            elif isinstance(val, int):
                total += val
        return total
    if aggregate == "distinct":
        seen: list[Any] = []
        for val in values:
            items = val if isinstance(val, list) else [val]
            for item in items:
                if item not in seen:
                    seen.append(item)
        return seen
    if len(values) == 1 and spec.get("grain") in ("estate", "subscription"):
        return values[0]
    return values


def search_fixture_corpus(corpus: list[dict[str, Any]], question: str, top: int = 5) -> list[dict[str, Any]]:
    """Deterministic word-match search over the offline KB fixture corpus, mirroring avd_concierge.search_brain."""
    words: list[str] = []
    for match in WORD_RE.finditer(question.lower()):
        w = match.group(0)
        if w not in words:
            words.append(w)
    if not words or top <= 0:
        return []
    patterns = [re.compile(rf"\b{re.escape(w)}(?:s|es)?\b", re.IGNORECASE) for w in words]
    scored: list[dict[str, Any]] = []
    for item in corpus:
        title = item.get("title") or ""
        snippet = item.get("snippet") or ""
        searchable = f"{item.get('ref', '')} {title} {snippet}"
        score = sum(1 for p in patterns if p.search(searchable))
        if not score:
            continue
        in_title = sum(1 for p in patterns if p.search(title))
        scored.append({
            "score": score,
            "inTitle": in_title,
            "kind": item.get("kind", "doc"),
            "ref": item["ref"],
            "title": title,
            "snippet": snippet,
        })
    scored.sort(key=lambda h: (-h["score"], -h["inTitle"], h["kind"] == "doc", h["ref"]))
    return scored[:top]


@dataclass
class CaseOutcome:
    """Result of executing one offline RAG eval case."""

    case_id: str
    category: str
    agent: str
    tool_outputs: list[tuple[str, bool]] = field(default_factory=list)
    observed_refs: set[str] = field(default_factory=set)
    citations: dict[str, Any] = field(default_factory=dict)
    kql_executions: int = 0
    snapshot_reads: int = 0
    shield_evidence: dict[str, Any] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.findings) == 0


def load_suite(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


async def run_case(root: Path, case: dict[str, Any], fixtures: dict[str, Any]) -> CaseOutcome:
    """Executes one case from evals/rag/cases.yml and returns its CaseOutcome."""
    agent = case.get("agent", "hosts")
    register = rag_tools.load_fact_register(root)
    catalog_queries = rag_tools.runbook_endpoint.catalog_queries(root)
    query_to_id = {text: qid for qid, text in catalog_queries.items()}
    snapshot = fixtures["snapshot"]
    kql_tables = fixtures["kql_tables"]
    kb_corpus = fixtures["kb_corpus"]
    poison_markers = [
        item["snippet"] for item in kb_corpus if item.get("attack_detected") and item.get("snippet")
    ]

    kql_executions = 0
    snapshot_reads = 0

    async def query_logs(kql: str) -> dict[str, Any]:
        nonlocal kql_executions
        kql_executions += 1
        qid = query_to_id.get(kql, "")
        table = kql_tables.get(qid, {"columns": [], "rows": []})
        return {
            "tables": [
                {
                    "name": "PrimaryResult",
                    "columns": [{"name": c, "type": "string"} for c in table["columns"]],
                    "rows": table["rows"],
                }
            ]
        }

    async def concierge_search_fn(question: str, top: int) -> dict[str, Any]:
        return {"hits": search_fixture_corpus(kb_corpus, question, top=top)}

    async def concierge_facts_fn(ids: list[str]) -> dict[str, Any]:
        nonlocal snapshot_reads
        snapshot_reads += 1
        resolved = {fid: resolve_c11_fact(snapshot, register[fid]) for fid in ids}
        return {
            "schema": "avd-concierge-facts/v1",
            "estate": snapshot.get("estate"),
            "generatedUtc": snapshot.get("generatedUtc"),
            "facts": resolved,
        }

    shield_mode = case.get("shield_mode")
    shield: Shield | None = None
    if shield_mode:
        async def fake_token() -> str:
            return "offline-token"

        async def fake_post(_url: str, _headers: dict, body: dict) -> dict:
            docs = body.get("documents") or []
            return {
                "userPromptAnalysis": {"attackDetected": False},
                "documentsAnalysis": [
                    {
                        "attackDetected": any(
                            "IGNORE PREVIOUS INSTRUCTIONS" in d or any(m in d for m in poison_markers)
                            for _ in [0]
                        )
                    }
                    for d in docs
                ],
            }

        shield = Shield(
            endpoint="https://cs-contoso.cognitiveservices.azure.com",
            token=fake_token,
            post=fake_post,
            mode=shield_mode,
        )

    runtime = rag_tools.RagRuntime(
        root=root,
        agent=agent,
        query_logs=query_logs,
        concierge_search=concierge_search_fn,
        concierge_facts=concierge_facts_fn,
        shield=shield,
    )

    tool_outputs: list[tuple[str, bool]] = []
    for call_spec in case.get("calls") or []:
        out_text, is_error = await runtime.call(call_spec["tool"], call_spec.get("args"))
        tool_outputs.append((out_text, is_error))

    citations = rag_tools.verify_citations(case.get("answer", ""), runtime.observed_refs)
    shield_ev = shield.evidence() if shield is not None else {"mode": "off"}
    findings = evaluate_expectations(case.get("expect") or {}, tool_outputs, citations, kql_executions, snapshot_reads, shield_ev)

    return CaseOutcome(
        case_id=case["id"],
        category=case["category"],
        agent=agent,
        tool_outputs=tool_outputs,
        observed_refs=set(runtime.observed_refs),
        citations=citations,
        kql_executions=kql_executions,
        snapshot_reads=snapshot_reads,
        shield_evidence=shield_ev,
        findings=findings,
    )


def evaluate_expectations(
    expect: dict[str, Any],
    tool_outputs: list[tuple[str, bool]],
    citations: dict[str, Any],
    kql_executions: int,
    snapshot_reads: int,
    shield_evidence: dict[str, Any],
) -> list[str]:
    """Compares a case's actual execution against its `expect` block and returns any mismatch descriptions."""
    findings: list[str] = []
    all_succeeded = all(not is_err for _, is_err in tool_outputs)
    if "all_calls_succeeded" in expect and all_succeeded != bool(expect["all_calls_succeeded"]):
        findings.append(f"all_calls_succeeded={all_succeeded}, expected {expect['all_calls_succeeded']}")

    combined_output = "\n".join(text for text, _ in tool_outputs)
    for err_sub in expect.get("error_substrings") or []:
        if err_sub not in combined_output:
            findings.append(f"missing expected error substring {err_sub!r} in tool outputs")

    if "kql_executions" in expect and kql_executions != int(expect["kql_executions"]):
        findings.append(f"kql_executions={kql_executions}, expected {expect['kql_executions']}")

    if "snapshot_reads" in expect and snapshot_reads != int(expect["snapshot_reads"]):
        findings.append(f"snapshot_reads={snapshot_reads}, expected {expect['snapshot_reads']}")

    if "withheld_documents" in expect and shield_evidence.get("withheldDocuments") != int(expect["withheld_documents"]):
        findings.append(
            f"withheldDocuments={shield_evidence.get('withheldDocuments')}, expected {expect['withheld_documents']}"
        )

    if "shield_marked" in expect and shield_evidence.get("marked") != expect["shield_marked"]:
        findings.append(f"shield marked={shield_evidence.get('marked')!r}, expected {expect['shield_marked']!r}")

    if "output_contains_stub" in expect and expect["output_contains_stub"] not in combined_output:
        findings.append(f"expected stub {expect['output_contains_stub']!r} in tool output")

    if "all_verified" in expect and citations.get("allVerified") != bool(expect["all_verified"]):
        findings.append(f"allVerified={citations.get('allVerified')}, expected {expect['all_verified']}")

    verified_set = set(citations.get("verified") or [])
    for wanted in expect.get("verified_contains") or []:
        if wanted not in verified_set:
            findings.append(f"missing expected verified citation {wanted!r} (got {sorted(verified_set)})")

    if "unverified" in expect:
        actual_unverified = list(citations.get("unverified") or [])
        expected_unverified = list(expect["unverified"])
        if actual_unverified != expected_unverified:
            findings.append(f"unverified={actual_unverified}, expected {expected_unverified}")

    return findings


def run_all(root: Path, suite_path: Path | None = None) -> list[CaseOutcome]:
    """Runs every case in evals/rag/cases.yml synchronously."""
    path = suite_path or (root / "evals/rag/cases.yml")
    suite = load_suite(path)
    fixtures = suite["fixtures"]
    cases = suite.get("cases") or []
    return [asyncio.run(run_case(root, case, fixtures)) for case in cases]
