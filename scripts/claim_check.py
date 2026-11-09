#!/usr/bin/env python3
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Section 4a deterministic code claim check and merge check (docs/spec.md section 4a): pure functions, stdlib only.

Verifies structured specialist claims (`{claim, value, unit, citations}`) against saved tool outputs and
the pinned C15 fact register (`contracts/fact-register.json`) before the coordinator merges them, and
checks that every fact-stating sentence in the merged answer cites a `supported` claim.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

SUPPORTED = "supported"
UNSUPPORTED = "unsupported"
UNCHECKED = "unchecked"

FACT_ID_RE = re.compile(r"\bc1[12]\.[A-Za-z0-9_.]+(?:@[A-Za-z0-9_.-]+)?")
BRACKET_CITATION_RE = re.compile(r"\[([^\[\]]+)\]")
NUMBER_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_.-])-?\d+(?:\.\d+)?(?![A-Za-z0-9_.-])")
FACTUAL_SENTENCE_RE = re.compile(
    r"(?:\b\d+(?:\.\d+)?\b|`[^`]+`|\b(?:sh|hp|rg|vnet|snet|ws|dag|cpc)-[A-Za-z0-9_-]+|\bc1[12]\.[A-Za-z0-9_.]+)"
)

CANONICAL_UNITS: dict[str, str] = {
    "usd": "usd",
    "$": "usd",
    "dollars": "usd",
    "%": "percent",
    "pct": "percent",
    "percent": "percent",
    "ms": "ms",
    "milliseconds": "ms",
    "s": "s",
    "sec": "s",
    "seconds": "s",
    "m": "min",
    "min": "min",
    "minutes": "min",
    "h": "hours",
    "hr": "hours",
    "hrs": "hours",
    "hours": "hours",
    "gb": "gb",
    "gib": "gib",
    "mb": "mb",
    "tb": "tb",
    "count": "count",
    "hosts": "count",
    "sessions": "count",
    "users": "count",
}


@dataclass(frozen=True)
class Claim:
    """One structured claim returned by a specialist in step 3 (fan-out)."""

    claim: str
    value: Any = None
    unit: str = ""
    citations: tuple[str, ...] = field(default_factory=tuple)

    @staticmethod
    def from_obj(obj: Claim | dict[str, Any]) -> Claim:
        if isinstance(obj, Claim):
            return obj
        if not isinstance(obj, dict):
            raise TypeError(f"Expected Claim or dict, got {type(obj).__name__}")
        raw_cites = obj.get("citations") or []
        cites = tuple(str(c).strip() for c in raw_cites if str(c).strip())
        return Claim(
            claim=str(obj.get("claim", "")).strip(),
            value=obj.get("value"),
            unit=str(obj.get("unit") or "").strip(),
            citations=cites,
        )


@dataclass(frozen=True)
class ClaimVerdict:
    """Step 4 verdict for a single claim."""

    claim: Claim
    status: str
    reasons: tuple[str, ...] = field(default_factory=tuple)
    matched_citations: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim.claim,
            "value": self.claim.value,
            "unit": self.claim.unit,
            "citations": list(self.claim.citations),
            "status": self.status,
            "reasons": list(self.reasons),
            "matchedCitations": list(self.matched_citations),
        }


def load_fact_register(root: Path) -> dict[str, dict[str, Any]]:
    """Loads contracts/fact-register.json (C15) indexed by fact ID."""
    path = root / "contracts/fact-register.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {f["id"]: f for f in (data.get("facts") or [])}


def _normalize_unit(unit: str) -> str:
    clean = unit.strip().lower()
    return CANONICAL_UNITS.get(clean, clean)


def _extract_fact_ids(claim: Claim) -> list[str]:
    found: list[str] = []
    for source in (claim.claim, *claim.citations):
        for match in FACT_ID_RE.finditer(source):
            token = match.group(0)
            if token not in found:
                found.append(token)
        if source.startswith(("c11.", "c12.")) and source not in found:
            found.append(source)
    return found


def _parse_iso_utc(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _check_snapshot_freshness(
    cid: str,
    output: Any,
    now_dt: datetime | None,
    max_age_days: int,
) -> list[str]:
    if not isinstance(output, dict):
        return []
    reasons: list[str] = []
    if output.get("confidence") == "low-confidence":
        reasons.append(f"low-confidence-snapshot:{cid}")
    if now_dt is not None:
        for key in ("windowEndUtc", "generatedUtc", "timestampUtc"):
            raw_ts = output.get(key)
            if isinstance(raw_ts, str):
                snap_dt = _parse_iso_utc(raw_ts)
                if snap_dt is not None:
                    age_days = (now_dt - snap_dt).total_seconds() / 86400.0
                    if age_days > max_age_days:
                        reasons.append(f"stale-snapshot-cited:{cid}")
                        break
    return reasons


def _collect_scalars_and_units(obj: Any, scalars: list[Any], units: set[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            k_lower = str(k).lower()
            if k_lower in ("unit", "currency", "billingcurrency") and isinstance(v, str) and v.strip():
                units.add(_normalize_unit(v))
            _collect_scalars_and_units(v, scalars, units)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _collect_scalars_and_units(item, scalars, units)
    else:
        scalars.append(obj)


def _as_decimal(val: Any) -> Decimal | None:
    if isinstance(val, bool) or val is None:
        return None
    if isinstance(val, int):
        return Decimal(val)
    if isinstance(val, float):
        return Decimal(str(val))
    if isinstance(val, str):
        clean = val.strip().replace(",", "")
        if clean.startswith("$"):
            clean = clean[1:].strip()
        if clean.endswith("%"):
            clean = clean[:-1].strip()
        if not clean:
            return None
        try:
            return Decimal(clean)
        except InvalidOperation:
            return None
    return None


def _number_matches_output(claimed: Decimal, output: Any) -> bool:
    scalars: list[Any] = []
    units: set[str] = set()
    _collect_scalars_and_units(output, scalars, units)

    exponent = claimed.as_tuple().exponent
    decimals = max(0, -int(exponent)) if isinstance(exponent, int) else 0
    tolerance = Decimal(10) ** (-decimals) * Decimal("0.5")

    candidates: list[Decimal] = []
    for item in scalars:
        dec = _as_decimal(item)
        if dec is not None:
            candidates.append(dec)
        elif isinstance(item, str):
            for match in NUMBER_TOKEN_RE.finditer(item):
                try:
                    candidates.append(Decimal(match.group(0)))
                except InvalidOperation:
                    pass

    if isinstance(output, str):
        for match in NUMBER_TOKEN_RE.finditer(output):
            try:
                candidates.append(Decimal(match.group(0)))
            except InvalidOperation:
                pass

    for actual in candidates:
        if actual == claimed:
            return True
        # Allow standard half-to-even or half-up rounding to the claimed precision when actual has finer precision
        actual_exp = actual.as_tuple().exponent
        actual_decimals = max(0, -int(actual_exp)) if isinstance(actual_exp, int) else 0
        if actual_decimals > decimals and abs(actual - claimed) <= tolerance:
            return True
    return False


def _value_matches_output(value: Any, unit: str, output: Any) -> tuple[bool, str | None]:
    scalars: list[Any] = []
    output_units: set[str] = set()
    _collect_scalars_and_units(output, scalars, output_units)

    # Also detect explicit unit markers in raw string outputs
    serialized = output if isinstance(output, str) else json.dumps(output, sort_keys=True)
    if "$" in serialized or "USD" in serialized:
        output_units.add("usd")
    if "%" in serialized:
        output_units.add("percent")

    norm_unit = _normalize_unit(unit) if unit else ""
    if norm_unit and output_units and norm_unit not in output_units:
        # If the output explicitly declares a unit and the claim declares a different incompatible unit, flag unit-mismatch
        return False, "unit-mismatch"

    if isinstance(value, bool):
        for item in scalars:
            if isinstance(item, bool) and item is value:
                return True, None
        return False, "value-not-in-output"

    claimed_dec = _as_decimal(value)
    if claimed_dec is not None and not (isinstance(value, str) and any(c.isalpha() for c in value)):
        if _number_matches_output(claimed_dec, output):
            return True, None
        return False, "value-not-in-output"

    target = str(value).strip()
    if not target:
        return False, "empty-value"

    for item in scalars:
        if isinstance(item, str) and item.strip() == target:
            return True, None
    # Exact token match inside serialized output (case-sensitive so swapped host/pool names fail)
    pattern = r"(?<![A-Za-z0-9_.-])" + re.escape(target) + r"(?![A-Za-z0-9_.-])"
    if re.search(pattern, serialized):
        return True, None
    return False, "value-not-in-output"


def check_claim(
    claim_input: Claim | dict[str, Any],
    tool_outputs: dict[str, Any],
    fact_register: dict[str, dict[str, Any]] | None = None,
    *,
    now_utc: str | None = None,
    max_snapshot_age_days: int = 90,
) -> ClaimVerdict:
    """Runs the section 4a deterministic code check on one specialist claim."""
    claim = Claim.from_obj(claim_input)
    reasons: list[str] = []

    if not claim.citations:
        return ClaimVerdict(claim=claim, status=UNSUPPORTED, reasons=("uncited",))

    missing = [cid for cid in claim.citations if cid not in tool_outputs]
    if missing:
        reasons.extend(f"missing-tool-call:{cid}" for cid in missing)

    if fact_register is not None:
        for fid in _extract_fact_ids(claim):
            base_id = fid.split("@", 1)[0]
            spec = fact_register.get(base_id)
            if spec is None:
                reasons.append(f"unregistered-fact:{base_id}")
            elif spec.get("status") != "built":
                reasons.append(f"unbuilt-fact:{base_id}")

    now_dt = _parse_iso_utc(now_utc) if now_utc else None
    for cid in claim.citations:
        if cid in tool_outputs:
            reasons.extend(_check_snapshot_freshness(cid, tool_outputs[cid], now_dt, max_snapshot_age_days))

    if reasons:
        return ClaimVerdict(claim=claim, status=UNSUPPORTED, reasons=tuple(reasons))

    # Inference claims (value is None) cannot be settled by code alone and wait for the step 4 model verifier.
    if claim.value is None:
        return ClaimVerdict(
            claim=claim,
            status=UNCHECKED,
            reasons=("inference-needs-verifier",),
            matched_citations=claim.citations,
        )

    matched: list[str] = []
    mismatch_reasons: list[str] = []
    for cid in claim.citations:
        ok, err = _value_matches_output(claim.value, claim.unit, tool_outputs[cid])
        if ok:
            matched.append(cid)
        elif err and err not in mismatch_reasons:
            mismatch_reasons.append(err)

    if matched and "unit-mismatch" not in mismatch_reasons:
        return ClaimVerdict(claim=claim, status=SUPPORTED, matched_citations=tuple(matched))

    return ClaimVerdict(
        claim=claim,
        status=UNSUPPORTED,
        reasons=tuple(mismatch_reasons or ["value-not-in-output"]),
    )


def check_claims(
    claims: Iterable[Claim | dict[str, Any]],
    tool_outputs: dict[str, Any],
    fact_register: dict[str, dict[str, Any]] | None = None,
    *,
    now_utc: str | None = None,
    max_snapshot_age_days: int = 90,
) -> list[ClaimVerdict]:
    """Checks a list of specialist claims in order."""
    return [
        check_claim(
            c,
            tool_outputs,
            fact_register,
            now_utc=now_utc,
            max_snapshot_age_days=max_snapshot_age_days,
        )
        for c in claims
    ]


def check_merged_answer(answer: str, verdicts: Iterable[ClaimVerdict]) -> dict[str, Any]:
    """Section 4a step 5: every sentence in the merged answer that states a fact must carry a citation
    to a `supported` claim, and must not cite an `unsupported` claim."""
    verdict_list = list(verdicts)
    supported_cites: set[str] = set()
    unsupported_cites: set[str] = set()
    for v in verdict_list:
        if v.status == SUPPORTED:
            supported_cites.update(v.claim.citations)
        elif v.status == UNSUPPORTED:
            unsupported_cites.update(v.claim.citations)

    raw_sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", (answer or "").strip()) if s.strip()]
    failing: list[str] = []
    for sentence in raw_sentences:
        # Sentences explicitly marking unconfirmed items ("not confirmed:") are allowed by spec section 4a step 5.
        if re.search(r"\bnot confirmed\b", sentence, re.IGNORECASE):
            continue
        is_factual = bool(FACTUAL_SENTENCE_RE.search(sentence) or BRACKET_CITATION_RE.search(sentence))
        if not is_factual:
            continue
        cited_in_sentence: list[str] = []
        for match in BRACKET_CITATION_RE.finditer(sentence):
            for part in re.split(r"[,;\s]+", match.group(1).strip()):
                if part:
                    cited_in_sentence.append(part)
        if not cited_in_sentence:
            failing.append(sentence)
            continue
        has_supported = any(c in supported_cites for c in cited_in_sentence)
        has_only_unsupported = any(c in unsupported_cites and c not in supported_cites for c in cited_in_sentence)
        if not has_supported or has_only_unsupported:
            failing.append(sentence)

    return {
        "valid": len(failing) == 0,
        "failingSentences": failing,
        "supportedCitations": sorted(supported_cites),
    }
