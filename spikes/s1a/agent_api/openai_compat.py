# avd-swarm S1a spike agent API: keyless Azure OpenAI adapter and fallback ladder. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Translates between Anthropic Messages tool-loop shapes and Azure OpenAI Chat Completions,
and implements the 3-tier model fallback ladder for the S1a Container App runtime:

  - Tier 1 (Primary):    `azure-openai` `gpt-4.1-nano` ($0.10 / $0.40 per 1M tokens)
  - Tier 2 (Fallback 1): `azure-openai` `gpt-4.1-mini` -> `gpt-4o-mini`
  - Tier 3 (Fallback 2): `anthropic` (`claude-haiku-5-5` / `claude-sonnet-5-5` / `claude-opus-5-5` via WIF)

Stdlib only, so CI tests run without installing container-only packages.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

AZURE_OPENAI_SCOPE = "https://cognitiveservices.azure.com/.default"
AZURE_OPENAI_API_VERSION = "2024-10-21"
AZURE_OPENAI_LADDER = ("gpt-4.1-nano", "gpt-4.1-mini", "gpt-4o-mini")
CLAUDE_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
ALL_MODELS = frozenset((*CLAUDE_MODELS, *AZURE_OPENAI_LADDER))


@dataclass(frozen=True)
class NormalizedUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def model_dump(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
        }


@dataclass(frozen=True)
class NormalizedBlock:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedResponse:
    stop_reason: str
    content: list[NormalizedBlock]
    usage: NormalizedUsage
    model: str = ""
    provider: str = "azure-openai"


def candidate_models_for(requested_model: str, ladder: tuple[str, ...] = AZURE_OPENAI_LADDER) -> list[str]:
    """Returns the ordered fallback list starting with `requested_model` if it is an Azure OpenAI model,
    followed by the remaining models in `ladder` without duplicates."""
    ordered: list[str] = []
    if requested_model in ladder:
        ordered.append(requested_model)
    for candidate in ladder:
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered


def to_openai_tools(anthropic_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Converts Anthropic tool definitions (`input_schema`) to OpenAI function tool definitions (`parameters`)."""
    out: list[dict[str, Any]] = []
    for tool in anthropic_tools:
        out.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description") or "",
                "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
            },
        })
    return out


def to_openai_tool_choice(tool_choice: dict[str, Any] | None) -> str | dict[str, Any] | None:
    """Converts Anthropic `tool_choice={'type': 'tool', 'name': '...'}` to OpenAI function tool_choice."""
    if not tool_choice:
        return None
    kind = tool_choice.get("type")
    if kind == "tool" and tool_choice.get("name"):
        return {"type": "function", "function": {"name": tool_choice["name"]}}
    if kind == "any":
        return "required"
    if kind == "none":
        return "none"
    return "auto"


def _block_to_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return block
    btype = getattr(block, "type", "")
    if btype == "text":
        return {"type": "text", "text": getattr(block, "text", "")}
    if btype == "tool_use":
        return {
            "type": "tool_use",
            "id": getattr(block, "id", ""),
            "name": getattr(block, "name", ""),
            "input": getattr(block, "input", {}) or {},
        }
    return {"type": str(btype)}


def to_openai_messages(system: str, anthropic_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Converts system prompt + Anthropic message history (including `tool_use` and `tool_result` blocks)
    into OpenAI Chat Completions messages."""
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})

    for msg in anthropic_messages:
        role = msg.get("role", "user")
        content = msg.get("content")
        if isinstance(content, str):
            messages.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            continue

        blocks = [_block_to_dict(b) for b in content]
        if role == "assistant":
            text_parts = [b.get("text", "") for b in blocks if b.get("type") == "text" and b.get("text")]
            tool_calls = []
            for b in blocks:
                if b.get("type") == "tool_use":
                    tool_calls.append({
                        "id": b.get("id") or f"call_{len(tool_calls) + 1}",
                        "type": "function",
                        "function": {
                            "name": b.get("name", ""),
                            "arguments": json.dumps(b.get("input") or {}),
                        },
                    })
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) if text_parts else None}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)
            continue

        # role == "user": may contain text blocks and/or tool_result blocks
        text_parts = []
        for b in blocks:
            btype = b.get("type")
            if btype == "text" and b.get("text"):
                text_parts.append(b["text"])
            elif btype == "tool_result":
                messages.append({
                    "role": "tool",
                    "tool_call_id": b.get("tool_use_id", ""),
                    "content": str(b.get("content") or ""),
                })
        if text_parts:
            messages.append({"role": "user", "content": "\n\n".join(text_parts)})

    return messages


def build_chat_completion_payload(
    *,
    system: str,
    tools: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    max_tokens: int,
    tool_choice: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Builds the JSON request payload for Azure OpenAI Chat Completions."""
    payload: dict[str, Any] = {
        "messages": to_openai_messages(system, messages),
        "max_completion_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = to_openai_tools(tools)
        mapped_choice = to_openai_tool_choice(tool_choice)
        if mapped_choice is not None:
            payload["tool_choice"] = mapped_choice
    return payload


def from_openai_chat_completion(
    payload: dict[str, Any],
    deployment_model: str = "",
    *,
    model_override: str = "",
) -> NormalizedResponse:
    """Converts an OpenAI Chat Completions JSON response into `NormalizedResponse`."""
    choices = payload.get("choices") or []
    choice = choices[0] if choices else {}
    message = choice.get("message") or {}
    finish_reason = choice.get("finish_reason") or "stop"

    blocks: list[NormalizedBlock] = []
    text = message.get("content")
    if isinstance(text, str) and text.strip():
        blocks.append(NormalizedBlock(type="text", text=text))

    raw_tool_calls = message.get("tool_calls") or []
    for idx, tc in enumerate(raw_tool_calls):
        fn = tc.get("function") or {}
        raw_args = fn.get("arguments") or "{}"
        try:
            parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            if not isinstance(parsed_args, dict):
                parsed_args = {}
        except (ValueError, TypeError):
            parsed_args = {}
        blocks.append(
            NormalizedBlock(
                type="tool_use",
                id=tc.get("id") or f"call_{idx + 1}",
                name=fn.get("name") or "",
                input=parsed_args,
            )
        )

    if raw_tool_calls or finish_reason == "tool_calls":
        stop_reason = "tool_use"
    elif finish_reason == "length":
        stop_reason = "max_tokens"
    elif finish_reason == "content_filter":
        stop_reason = "refusal"
    else:
        stop_reason = "end_turn"

    raw_usage = payload.get("usage") or {}
    prompt_tokens = int(raw_usage.get("prompt_tokens") or 0)
    completion_tokens = int(raw_usage.get("completion_tokens") or 0)
    details = raw_usage.get("prompt_tokens_details") or {}
    cached_tokens = int(details.get("cached_tokens") or 0)
    uncached_input = max(0, prompt_tokens - cached_tokens)

    usage = NormalizedUsage(
        input_tokens=uncached_input,
        output_tokens=completion_tokens,
        cache_read_input_tokens=cached_tokens,
        cache_creation_input_tokens=0,
    )
    resolved_model = model_override or deployment_model or str(payload.get("model") or "")
    return NormalizedResponse(
        stop_reason=stop_reason,
        content=blocks,
        usage=usage,
        model=resolved_model,
        provider="azure-openai",
    )


def chat_completions_url(endpoint: str, deployment: str, api_version: str = AZURE_OPENAI_API_VERSION) -> str:
    base = endpoint.rstrip("/")
    return f"{base}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"


async def call_with_fallback_ladder(
    candidates: list[str],
    invoke_deployment: Callable[[str], Awaitable[Any]],
    *,
    non_retriable: tuple[type[BaseException], ...] = (),
) -> tuple[Any, str, list[dict[str, str]]]:
    """Tries each deployment in `candidates` in order. If a deployment fails with a transient/quota/not-found
    error, records the attempt and falls back to the next deployment in the ladder."""
    attempts: list[dict[str, str]] = []
    last_error: Exception | None = None
    for model_name in candidates:
        try:
            response = await invoke_deployment(model_name)
            attempts.append({"model": model_name, "status": "ok"})
            return response, model_name, attempts
        except BaseException as err:
            if non_retriable and isinstance(err, non_retriable):
                raise
            if not isinstance(err, Exception):
                raise
            attempts.append({"model": model_name, "status": "error", "error": f"{type(err).__name__}: {err}"})
            last_error = err
    assert last_error is not None
    raise last_error
