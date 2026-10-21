# avd-swarm gateway: the kill switch flag check. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Every model call reads the switch position first (governance section 2.3 layer 6, red team R21).

The position is a tag on the swarm's resource group (layer 1), set by the switch's Logic App or a
person's Cloud Shell block. A position that forbids agents refuses the call; so does a flag that can't
be read, a missing tag or a value that isn't a position. Stdlib only; the HTTP call is passed in.
"""

from __future__ import annotations

from typing import Callable

from .prices import SpendRefused

POSITIONS = ("run", "halt-actions", "halt-agents", "halt-all")
ARM = "https://management.azure.com"
ARM_SCOPE = "https://management.azure.com/.default"
RG_API_VERSION = "2021-04-01"

FlagReader = Callable[[], str]  # returns the position; raises when it can't read it


def position_from_tags(tags: dict | None, tag: str) -> str:
    """The position in a resource group's tags. ARM omits an empty tags object, so None means no tag."""
    value = (tags or {}).get(tag)
    if value not in POSITIONS:
        raise ValueError(f"tag {tag} is {'missing' if value is None else 'not a switch position'}")
    return value


def arm_flag_reader(get_json: Callable[[str], dict], subscription: str, resource_group: str, tag: str) -> FlagReader:
    """A reader that GETs the resource group through ARM (Reader on that group is enough).
    get_json(url) signs in with the caller's managed identity and raises on any non-2xx."""
    url = (f"{ARM}/subscriptions/{subscription}/resourcegroups/{resource_group}"
           f"?api-version={RG_API_VERSION}")
    return lambda: position_from_tags(get_json(url).get("tags"), tag)


REQUIRED_REFUSALS = ("halt-agents", "halt-all")


def refusals(config: dict) -> tuple[str, ...]:
    """The positions that refuse model calls, from governance/gateway.yml. Fails closed: a value that isn't a
    list of positions, or one that leaves out halt-agents or halt-all, is a configuration error, never a
    quieter switch (governance section 2.3)."""
    value = (config.get("flag") or {}).get("refuseModelCallsAt")
    if not isinstance(value, list) or not all(isinstance(p, str) and p in POSITIONS for p in value):
        raise ValueError(f"flag.refuseModelCallsAt must be a list of positions from {POSITIONS}")
    missing = [p for p in REQUIRED_REFUSALS if p not in value]
    if missing:
        raise ValueError(f"flag.refuseModelCallsAt must include {missing}: those positions forbid agents")
    return tuple(value)


def check(read: FlagReader, refuse_at: tuple[str, ...] | list[str]) -> str:
    """Returns the position when model calls are allowed; raises SpendRefused otherwise."""
    try:
        position = read()
    except Exception as error:  # any failure to read is a refusal (spend.md section 3.7 item 1)
        raise SpendRefused(f"kill switch flag unreadable, refusing: {type(error).__name__}: {error}"[:300]) from error
    if position not in POSITIONS:
        raise SpendRefused(f"kill switch flag {position!r} is not a position, refusing")
    if position in refuse_at:
        raise SpendRefused(f"kill switch at {position}: no model calls")
    return position
