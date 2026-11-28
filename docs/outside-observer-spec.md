# Outside Observer Specification: Near-Zero-Cost Hardened External Observer

Status: proposed (2026-11-28).

> [!NOTE]
> Personal community research project, not for production, provided "as is" with no warranty, and not affiliated with the author's employer, Liquidware, ServiceNow, or Microsoft.

This specification defines a **near-zero-cost (~\$0.02–\$0.05/month)** external observer architecture for `avd-swarm` implementing [governance.md](governance.md) §2.2, §2.3, and §2.4 without requiring a billable Log Analytics workspace or Microsoft Sentinel ingestion.

---

## 1. Design Goals

* **Outside `avd-swarm`'s trust boundary:** Deployed in an isolated resource group (or separate subscription) where **no `avd-swarm` identity holds any RBAC role**.
* **Corroborate, never trust (`R9`):** Reads Azure's own control-plane and identity records (Activity Log and Entra Audit/Sign-in logs via Microsoft Graph `GET`, both \$0 ingestion) rather than trusting `avd-swarm`'s event stream alone.
* **No conversation text (`GQ1`):** Observers receive only signed change-workflow and security CloudEvents—never prompts, chat history, or model outputs.
* **Fail closed:** Observer silence (>15 minutes) or uncorroborated writes trip the kill switch (`halt-actions` or `halt-all`); no observer or agent can ever reset the switch.

---

## 2. Three-Layer Architecture

### Layer 1 — External Corroborator (Hardened Azure Function, Consumption Plan)
* **Compute & Cost:** Azure Functions Consumption plan (1,000,000 free executions/month; a 5-minute timer uses ~8,640 executions/month, leaving only pennies/month for the minimal storage account).
* **Identity:** Dedicated User-Assigned Managed Identity with **read-only** permissions (`Reader` on target AVD resource groups, `Storage Blob Data Reader` on the immutable audit blob container, Microsoft Graph `AuditLog.Read.All` and `Directory.Read.All`) plus permission **only** to call the Kill Switch Logic App's `trip` endpoint (`templates/kill-switch/main.bicep`). It holds no write, role-assignment, credential-creation, or switch-reset rights.
* **5-Minute Corroboration Loop:**
  1. Reads Azure Activity Log writes and Entra Audit/Sign-in logs for all registered `avd-swarm` identities.
  2. Cross-checks every observed write against signed `plan.approved` CloudEvents in the immutable blob audit store.
  3. Checks `avd-swarm` `heartbeat` freshness (`observerSilenceMinutes: 15`) and token-meter state vs. `halt-agents`.
  4. On any mismatch, unapproved write, out-of-scope write, or post-halt model usage: calls `trip(halt-all)` and sends an owner notification.
  5. On every healthy run: pings an external dead-man's-switch URL (e.g., Healthchecks.io free tier) so a silent/deleted Function is detected outside Azure and GitHub.

### Layer 2 — Azure Activity Log Alerts + Action Group (Zero-Workspace Tripwire)
* **Scope:** Azure Monitor Activity Log Alerts in the isolated observer resource group (no Log Analytics workspace or Sentinel required).
* **Triggers:** Uncorrelated writes by the `approve` identity, `roleAssignments/write`, `policyExemptions/write`, `Microsoft.Security/*` writes, and any Azure SRE Agent write.
* **Target:** Action Group wired directly to the Kill Switch Logic App `trip` endpoint and owner notification.

### Layer 3 — External `verdict` Webhook (Step 4+ / Second-Opinion Gate)
* **Endpoint:** Stateless HTTP-triggered Function (Entra/HMAC authenticated) receiving **only** signed `plan.proposed` CloudEvents (`governance.md` §2.2).
* **Evaluation:**
  1. Validates strict JSON schema and catalog enum allowlists before any model call.
  2. Runs deterministic invariants (blast radius, time window, scope, `C26` cloud boundary, `D1` data-loss guard).
  3. Optionally queries a cheap second-opinion model on a **separate provider/account** from `avd-swarm`'s agents (pinned price, capped `max_tokens`).
  4. Tracks **approval-fatigue telemetry** from `C10` outcomes (sub-3-second approval streaks or 0% rejection streaks), escalating `observer: required` or tripping `halt-actions` on rubber-stamp streaks.
* **Fail-closed:** Timeout or 15-minute silence counts as `deny` / `halt-actions`.

---

## 3. Red Team & Invariants

| Threat / Failure Mode | Required Mitigation |
|---|---|
| **Compromised `avd-swarm` disables the Function** | Zero `avd-swarm` RBAC on the observer RG/subscription; Function identity can only read logs and call monotonic `trip` (cannot reset, create credentials, or write AVD). |
| **Function silently stopped or Azure outage** | External dead-man's-switch ping on every healthy run; `avd-swarm` gate fails closed (`halt-actions`) after 15 minutes of observer silence. |
| **Prompt injection inside `plan.proposed` fields** | Never send conversation text; enforce strict schema + enum allowlists before any second-opinion model call; deterministic rules run first and override the model. |
| **Endpoint or quota DoS via `plan.proposed` / `trip` spam** | Entra/HMAC auth on HTTP endpoints; strict rate limits and `max_tokens` cap; `trip` is idempotent and monotonic (`run → halt-actions → halt-agents → halt-all`). |
| **Approval fatigue turning `approve` into theater** | Observer tracks approval latency and consecutive-approval streaks, tightening `observer: required` or tripping `halt-actions` on suspicious streaks. |

---

## 4. Build Alignment (`spec.md` §9a)

* **Now / Step 2–3:** Layer 1 (Corroborator Function) and Layer 2 (Activity Log Alerts) can be built and unit-tested offline immediately alongside `templates/kill-switch/main.bicep` and `governance/watchdog.yml`.
* **Step 4+:** Layer 3 (`verdict` webhook and second-opinion model) activates when plans requiring `observer: required` are introduced, exercised first in step 4's red team.

---

## 5. Implementation Layout

* `observer/` — Python 3.12 Azure Functions Consumption package (`function_app.py`, `corroborator.py`, `verdict.py`, `models.py`, `auth.py`).
* `templates/outside-observer/main.bicep` — Isolated-RG Bicep template deploying the User-Assigned Managed Identity, immutable audit blob container, Consumption Function App (Layer 1 + Layer 3), and zero-workspace Activity Log Alerts + Action Group (Layer 2).
* `templates/outside-observer/avd-read-rbac.bicep` — Optional cross-RG read-only `Reader` assignment for the observer UAMI on target AVD resource groups (zero write/reset roles).
* `templates/outside-observer/Test-OutsideObserver.ps1` — Offline Bicep build, RBAC isolation verification, and monotonic `trip` dry-run helper.
* `scripts/check_kill_switch.py` & `scripts/tests/test_outside_observer.py` — Offline CI invariants and unit tests.
