# avd-swarm gate: the evidence provenance rule. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""V1-4: a plan needs evidence from a thread an operator started (spec section 3a; red team V1-4).

Every finding and every evidence item carries `threadStartedBy`. Only `avd-buddy` means an operator asked;
`schedule` and `manual` (the check runner) and `alert` (the alert path, core-and-adapters.md WP6) are runs
nobody in avd-buddy started, so they may support a plan but never carry it alone. A value outside the
vocabulary, or a missing one, counts as not operator-started: the rule fails closed.
"""

from __future__ import annotations

OPERATOR = "avd-buddy"
UNATTENDED = ("schedule", "manual", "alert")
VOCABULARY = (OPERATOR, *UNATTENDED)


def refusals(plan: object) -> list[str]:
    """Why the gate refuses this plan under V1-4; empty when it passes. plan: {"evidence": [{...}, ...]}."""
    evidence = plan.get("evidence") if isinstance(plan, dict) else None
    if not isinstance(evidence, list) or not evidence:
        return ["V1-4: a plan needs evidence"]
    found, unknown = [], []
    for item in evidence:
        started = item.get("threadStartedBy") if isinstance(item, dict) else None
        found.append(started)
        if started not in VOCABULARY:
            unknown.append(repr(started))
    out = [f"V1-4: threadStartedBy {u} isn't in {VOCABULARY}" for u in sorted(set(unknown))]
    if OPERATOR not in found:
        kinds = sorted({str(s) for s in found})
        out.append(f"V1-4: no evidence from a thread an operator started in avd-buddy (only {', '.join(kinds)})")
    return out
