# Evaluations (`evals/`)

> **Personal project, not for production, as is.** Not affiliated with the author's employer, Liquidware or Microsoft.

Evaluation suites for `avd-swarm`, covering the S1a spike, the Step 2/S1 Desktop DevOps agents (`coordinator`, `hosts`, `cost`, `intune`), the 3-leg hybrid RAG and citation verifier, and `avd-buddy` voice transcripts.

Per `governance/protected-paths.yml`, `evals/**` and `spikes/s1a/evals/**` are **append-only**: add new cases; never edit or delete existing cases in an agent-authored PR.

## Suites

| Suite | File | Scope | How to run |
|---|---|---|---|
| **S1a spike (10 + 2 probes)** | [`spikes/s1a/evals/questions.json`](../spikes/s1a/evals/questions.json) | 10 AVD estate & refusal questions + 2 Q4 boundary probes | `spikes/s1a/tools/run_evals.py` (live lab) or `scripts/check_evals.py` (offline schema) |
| **Per-agent (5+ per agent)** | [`evals/agents/cases.yml`](agents/cases.yml) | 5 cases each for `coordinator`, `hosts`, `cost`, and `intune` (routing, allowlist reads, KQL/C11 citations, write/mutation refusals) | `python3 scripts/check_evals.py` / `python3 -m unittest scripts/tests/test_evals.py` |
| **3-leg hybrid RAG & citations** | [`evals/rag/cases.yml`](rag/cases.yml) | `kql_catalog_run`, `concierge_search`, `concierge_facts`, `verify_citations`, Prompt Shields withholding | `python3 -m unittest scripts/tests/test_rag_eval.py` |
| **Voice normalization** | [`evals/voice/cases.yml`](voice/cases.yml) | Spoken-to-canonical AVD term normalization (`avd-buddy` voice mode) | `python3 -m unittest scripts/tests/test_voice_check.py` |

## Exit gates

- **Step 1 (S1a spike):** All 10 `eval` questions answered or replaced with run cost and teardown evidence (`docs/spec.md` §9a).
- **Step 2 (Read-only, side by side):** Answers cite evidence in at least 80% of eval questions (`docs/spec.md` §9a); `verify_citations` passes deterministically with zero Prompt Shields leakage (`evals/rag/cases.yml`).
- **Offline CI gate:** `python3 scripts/check_evals.py` and `python3 -m unittest discover -s scripts/tests` run on every PR with zero cloud or token spend.
