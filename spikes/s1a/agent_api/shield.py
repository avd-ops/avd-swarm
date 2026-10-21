# avd-swarm S1a spike: Prompt Shields scan and decoy tripwire. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Untrusted text is scanned in the model client, whichever provider the agents run on (red team R26,
docs/security-tooling.md sections 3 and 4).

Every tool result is split into documents and sent to Azure AI Content Safety's Prompt Shields before
it goes back to the model; the operator's question is sent as the user prompt. The call signs in with
the agents' managed identity (Cognitive Services User on the one resource, local auth disabled), so no
key exists. On a hit:
  observe  the text goes through unchanged and the hit is counted (step 1, and step 2 until the
           false-positive rate is known)
  enforce  each flagged document is replaced by STUB and the conversation is marked
Text past RESULT_CAP_CHARS is never sent (it would run up the bill): observe passes it on unscanned and
counts it; enforce withholds it. If Content Safety can't be reached, the text goes through, counted as
unscanned, and the conversation is marked unscanned.

The tool layer also counts calls to tools outside the agent's allowlist and to its decoy tools, a tool
a healthy agent never has reason to call (idea from Beelzebub's MCP honeypot; no code from it).

Events carry counts, the agent and the tool name, never the text (governance section 2.2).
Stdlib only, so the guard's unit tests run it without the spike's dependencies; the HTTP call is passed in.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable

API_VERSION = "2024-09-01"
SCOPE = "https://cognitiveservices.azure.com/.default"
DOCS_PER_CALL = 5  # Prompt Shields takes up to five documents per call...
DOC_CHARS = 2_000  # ...with 10K characters in all, so five of these fill one call
PROMPT_CHARS = 10_000  # the user prompt's own limit
RESULT_CAP_CHARS = 20_000  # per tool result: at most 20 billed records
RECORD_CHARS = 1_000  # Content Safety bills per text record of up to 1,000 characters
MODES = ("observe", "enforce")
STUB = "[withheld: possible prompt injection, evidence {id}]"
CAP_STUB = "[withheld: {chars} characters past the scan cap, evidence {id}]"

# One decoy for the Hosts agent: plausible next to the virtualdesktop tools, never needed to answer a
# read-only question. A call returns a neutral error and counts as decoy.tripped.
DECOY_TOOLS = [{
    "name": "export_session_recordings",
    "description": "Export the recorded user sessions of a host pool to a storage account for review.",
    "input_schema": {
        "type": "object",
        "properties": {
            "hostPool": {"type": "string", "description": "Host pool name"},
            "storageAccount": {"type": "string", "description": "Destination storage account"},
        },
        "required": ["hostPool", "storageAccount"],
    },
}]
DECOY_ERROR = "{name} is not available right now"

Post = Callable[[str, dict, dict], Awaitable[dict]]  # (url, headers, body) -> response JSON; raises on failure


def chunk(text: str, size: int = DOC_CHARS, cap: int = RESULT_CAP_CHARS) -> tuple[list[str], str]:
    """Split text into documents of at most size characters, up to cap; returns (documents, the rest past cap)."""
    if size <= 0 or cap <= 0:
        raise ValueError("size and cap must be positive")
    scanned, rest = text[:cap], text[cap:]
    return [scanned[i:i + size] for i in range(0, len(scanned), size)], rest


def batches(documents: list[str], per_call: int = DOCS_PER_CALL) -> list[list[str]]:
    return [documents[i:i + per_call] for i in range(0, len(documents), per_call)]


def records(texts: list[str]) -> int:
    """Billed text records: each text counts as ceil(len / 1,000), at least one."""
    return sum(max(1, math.ceil(len(t) / RECORD_CHARS)) for t in texts)


def request_body(documents: list[str] | None = None, user_prompt: str | None = None) -> dict:
    if documents and len(documents) > DOCS_PER_CALL:
        raise ValueError(f"at most {DOCS_PER_CALL} documents per call")
    if documents and sum(len(d) for d in documents) > DOCS_PER_CALL * DOC_CHARS:
        raise ValueError(f"at most {DOCS_PER_CALL * DOC_CHARS} document characters per call")
    if user_prompt is not None and len(user_prompt) > PROMPT_CHARS:
        raise ValueError(f"at most {PROMPT_CHARS} user prompt characters per call")
    # The API wants both keys; an empty prompt or list means nothing to analyze there.
    return {"userPrompt": user_prompt or "", "documents": list(documents or [])}


def document_hits(response: dict, sent: int) -> list[bool]:
    """attackDetected for each document sent, in order. A response that doesn't cover every document is an error."""
    analysis = response.get("documentsAnalysis") or []
    if len(analysis) != sent:
        raise ValueError(f"Prompt Shields analyzed {len(analysis)} of {sent} documents")
    return [bool(a.get("attackDetected")) for a in analysis]


def prompt_hit(response: dict) -> bool:
    return bool((response.get("userPromptAnalysis") or {}).get("attackDetected"))


def assemble(documents: list[str], hits: list[bool], rest: str, mode: str, evidence_id: str) -> str:
    """The text the model gets: observe returns everything; enforce stubs flagged documents and the rest past the cap."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if mode == "observe":
        return "".join(documents) + rest
    parts = [STUB.format(id=evidence_id) if hit else doc for doc, hit in zip(documents, hits)]
    if rest:
        parts.append(CAP_STUB.format(chars=len(rest), id=evidence_id))
    return "".join(parts)


def classify_call(name: str, allowed: list[str], decoys: list[str]) -> str:
    """allowed, decoy or unknown. A decoy wins over the allowlist, so listing one there can't silence it."""
    if name in decoys:
        return "decoy"
    return "allowed" if name in allowed else "unknown"


def decoys_for(offered: list[str]) -> list[dict]:
    """The decoy tools to add, skipping any whose name a real tool already has."""
    return [d for d in DECOY_TOOLS if d["name"] not in offered]


@dataclass
class Shield:
    """One conversation's scanner. endpoint '' turns it off: every text passes through and nothing is counted."""

    endpoint: str
    token: Callable[[], Awaitable[str]]
    post: Post
    mode: str = "observe"
    counts: dict = field(default_factory=lambda: {
        "shieldHits": 0, "promptHits": 0, "scannedRecords": 0, "unscannedChars": 0, "shieldErrors": 0,
        "withheldDocuments": 0, "unknownToolCalls": 0, "decoyCalls": 0})
    events: list = field(default_factory=list)
    marked: str = ""  # '', 'flagged' or 'unscanned': why plans from this conversation would need the observer

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")

    @property
    def on(self) -> bool:
        return bool(self.endpoint)

    def mark(self, reason: str) -> None:
        if self.marked != "flagged":  # flagged outranks unscanned
            self.marked = reason

    async def _call(self, body: dict) -> dict:
        url = f"{self.endpoint.rstrip('/')}/contentsafety/text:shieldPrompt?api-version={API_VERSION}"
        headers = {"Authorization": f"Bearer {await self.token()}", "Content-Type": "application/json"}
        return await self.post(url, headers, body)

    async def scan_result(self, text: str, agent: str, tool: str) -> str:
        """A tool result, scanned before the model sees it; returns the text to give the model."""
        if not self.on or not text:
            return text
        documents, rest = chunk(text)
        hits: list[bool] = []
        try:
            for batch in batches(documents):
                hits += document_hits(await self._call(request_body(documents=batch)), len(batch))
                self.counts["scannedRecords"] += records(batch)
        except Exception:  # reads continue, unscanned (security-tooling.md section 3)
            self.counts["shieldErrors"] += 1
            self.counts["unscannedChars"] += len(text)
            self.mark("unscanned")
            return text
        self.counts["unscannedChars"] += len(rest)
        if rest:
            self.mark("unscanned")
        found = sum(hits)
        evidence_id = uuid.uuid4().hex[:12]
        if found:
            self.counts["shieldHits"] += found
            self.mark("flagged")
            self.events.append({"type": "injection.suspected", "agent": agent, "tool": tool, "count": found,
                                "evidence": evidence_id})
            if self.mode == "enforce":
                self.counts["withheldDocuments"] += found
        return assemble(documents, hits, rest, self.mode, evidence_id)

    async def scan_prompt(self, text: str, agent: str) -> None:
        """The operator's own message, sent as the user prompt. Counted only: the operator sees their own question."""
        if not self.on or not text:
            return
        try:
            hit = prompt_hit(await self._call(request_body(user_prompt=text[:PROMPT_CHARS])))
            self.counts["scannedRecords"] += records([text[:PROMPT_CHARS]])
        except Exception:
            self.counts["shieldErrors"] += 1
            self.mark("unscanned")
            return
        if hit:
            self.counts["promptHits"] += 1
            self.mark("flagged")
            self.events.append({"type": "injection.suspected", "agent": agent, "tool": "(operator prompt)", "count": 1})

    def tool_call(self, name: str, allowed: list[str], agent: str) -> str:
        """Counts a call the tool layer refuses; returns its classification. Works with the scan off."""
        kind = classify_call(name, allowed, [d["name"] for d in DECOY_TOOLS])
        if kind == "decoy":
            self.counts["decoyCalls"] += 1
            self.mark("flagged")
            self.events.append({"type": "decoy.tripped", "agent": agent, "tool": name})
        elif kind == "unknown":
            self.counts["unknownToolCalls"] += 1
        return kind

    def evidence(self) -> dict:
        """Counts and events for the job's evidence: never the text."""
        return {"mode": self.mode if self.on else "off", "marked": self.marked, **self.counts, "events": list(self.events)}
