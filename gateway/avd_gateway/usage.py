# avd-swarm gateway: usage events for the observer. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""What the gateway tells the observer about each call: counts and dollars, never text (governance section 2.2)."""

from __future__ import annotations

TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def usage_event(caller: str, run_id: str, provider: str, model: str, usage: dict, cost_usd: float) -> dict:
    return {"type": "model.usage", "caller": caller, "run": run_id, "provider": provider, "model": model,
            **{f: int(usage.get(f) or 0) for f in TOKEN_FIELDS}, "costUsd": round(cost_usd, 6)}


def refused_event(caller: str, run_id: str, reason: str) -> dict:
    """A refused call. The reason is the gateway's own message (a cap, the flag, a price), never model text."""
    return {"type": "model.refused", "caller": caller, "run": run_id, "reason": reason[:200]}
