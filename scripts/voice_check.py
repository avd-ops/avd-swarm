#!/usr/bin/env python3
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""avd-buddy's voice rules (docs/buddy-voice.md): pure functions, stdlib only.

Code, not the model, decides the mode of a turn and whether a reply may show an aside, and checks
the aside before avd-buddy shows it. The settings live in governance/buddy-voice.yml (protected).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

LIGHT, SOFT_STEADY, HARD_STEADY = "light", "soft-steady", "hard-steady"


@dataclass
class Turn:
    """What code knows about a turn before the model answers: events from the tool layer, the operator's words."""

    events: set[str] = field(default_factory=set)
    operator_text: str = ""
    language: str = "en"
    humor_setting: str = "light"  # the operator's own setting: "light" or "off"
    replies_since_aside: int = 99
    model_asked_steady: bool = False  # the model may ask for steady mode, never for light


def _words_in(text: str, words: list[str]) -> list[str]:
    hits = []
    for w in words:
        if re.search(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", text, re.IGNORECASE):
            hits.append(w)
    return hits


def _is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return cp >= 0x1F000 or 0x2600 <= cp <= 0x27BF or 0x2B00 <= cp <= 0x2BFF or cp == 0xFE0F


def get_mode(turn: Turn, settings: dict) -> str:
    """The turn's mode. Hard steady wins over soft steady, which wins over light."""
    if turn.events & set(settings["hard_steady_events"]):
        return HARD_STEADY
    if (
        turn.events & set(settings["soft_steady_events"])
        or _words_in(turn.operator_text, settings["stress_words"])
        or turn.model_asked_steady
    ):
        return SOFT_STEADY
    return LIGHT


def aside_allowed(turn: Turn, settings: dict) -> bool:
    """Whether avd-buddy may show an aside this turn. Off means the front end drops it unread."""
    aside = settings["aside"]
    if turn.humor_setting != "light" or turn.language not in aside["languages"]:
        return False
    if turn.replies_since_aside < aside["min_replies_between"]:
        return False
    mode = get_mode(turn, settings)
    if mode == HARD_STEADY:
        return False
    if mode == SOFT_STEADY:
        return bool(_words_in(turn.operator_text, settings["levity_invites"]))
    return True


def get_aside_findings(aside: str, settings: dict, estate_names: list[str] | None = None) -> list[str]:
    """Every reason this aside can't be shown. Empty means it may be shown, if aside_allowed says so."""
    findings = []
    text = aside.strip()
    if len(text) > settings["aside"]["max_chars"]:
        findings.append("too-long")
    if len(re.findall(r"[.!?](\s|$)", text)) > 1:
        findings.append("more-than-one-sentence")
    if any(_is_emoji(ch) for ch in text):
        findings.append("emoji-or-symbol")
    for name, pattern in settings["aside_forbidden_patterns"].items():
        if re.search(pattern, text):
            findings.append(f"carries-{name}")
    for topic, words in settings["banned_terms"].items():
        if _words_in(text, words):
            findings.append(f"banned-{topic}")
    if estate_names and _words_in(text, estate_names):
        findings.append("carries-estate-name")
    return findings


def check_reply(turn: Turn, aside: str | None, settings: dict, estate_names: list[str] | None = None) -> list[str]:
    """Findings for one reply as the eval sees it: an aside where none is allowed, or a bad aside."""
    if not aside:
        return []
    if not aside_allowed(turn, settings):
        return [f"aside-in-{get_mode(turn, settings)}"]
    return get_aside_findings(aside, settings, estate_names)
