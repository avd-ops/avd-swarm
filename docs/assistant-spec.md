# `avd-assistant` — Interactive Assistant & Zero-Token-First Architecture Spec

> **Personal research project — not for production.** Provided as is, without warranty; not affiliated with the author's employer, Liquidware or Microsoft.

- **Status:** Proposed (2026-11-17) — updated with Fable red-team fixes (`F1`–`F19`).
- **Supersedes / Evolves:** [`buddy-app.md`](buddy-app.md), [`buddy-voice.md`](buddy-voice.md) (renaming `avd-buddy` → `avd-assistant` and demoting Buddy to one of four interactive modes).
- **Builds on:** [`decisions/0010-avd-buddy-container-app-hosting.md`](decisions/0010-avd-buddy-container-app-hosting.md), [`spend.md`](spend.md), [`redteam.md`](redteam.md).

---

## 1. Purpose & Core Shift

`avd-assistant` (UI short name: **Assistant**) replaces `avd-buddy` as the installable PWA front end and interactive assistant for `avd-swarm`.

Two core shifts drive this redesign:
1. **Multi-mode interactive assistant:** Instead of a single fixed persona, Assistant provides four user-selectable lenses (**Chief of Staff**, **Red Team**, **Guru**, **Buddy**). **Buddy** becomes one mode rather than the product identity.
2. **Deterministic-first, LLM-as-last-resort:** Assistant is laser-focused on mechanical `avd-ops` operations and organizational second-brain (`avd-second-brain`) queries. It answers as much as possible at **0 tokens** using deterministic code, pinned local indexing, and structured plan/status data; rejects off-topic general chat without calling an LLM; and invokes an LLM **only** when explicitly warranted.

---

## 2. Identity, Branding & Icon (Owner Decisions Recorded)

- **Product name:** `avd-assistant` (UI title / PWA `short_name`: **Assistant**). Chosen over `avd-opsbot` to avoid colliding with `avd-landing-zone`'s delegated Graph operator app registration (`avd-opsbot`) and to follow `avd-current-state`'s rule against sharing a name with a write-capable app.
- **Public static demo:** `assistant.avd-ops.dev` (`?sim=1` synthetic fixtures only, zero Azure spend, research-only notice first, no external fonts or scripts).
- **Live adopter deployment:** Scale-to-zero Azure Container App (`minReplicas: 0`) with Entra ID Easy Auth and GHCR digest-pinned images per [decision 0010](decisions/0010-avd-buddy-container-app-hosting.md).
- **Icon (Shield + `><` Visor):**
  - Replaces the Clippy-style paperclip mascot in `assets/buddy/avd-buddy-icon.svg` (owner decision recorded in `assets/assistant/REVIEW.md`).
  - Vector mark (`assets/assistant/avd-assistant-icon.svg`) using `avd-ops` Design System 3.0 tokens (`--brand` `#1ca998`, `--brand-ink` `#0d1015`): the `avd-ops` shield silhouette housing a dark geometric visor with `><` remote-session chevrons as the bot's eyes/prompt.
  - Built into the full PWA icon set (`192`, `512`, `maskable-512`, `monochrome-512`, `apple-touch-icon` `180`, `favicon.svg`, `favicon-32.png`, `avatar.svg`).

---

## 3. Security & Control-Plane Invariants

All invariants from [`buddy-app.md`](buddy-app.md) §2 and [`buddy-voice.md`](buddy-voice.md) §1 remain mandatory, hardened by Fable findings `F1`–`F4`:

1. **Front end only / no in-app approvals:** Assistant never writes to Azure or approves plans. Every change is a plan in the plan store approved by a person in the GitHub `approve` environment.
2. **No tokens or secrets in the browser:** Entra ID Easy Auth (`HttpOnly` `AppServiceAuthSession` cookie + `X-MS-CLIENT-PRINCIPAL` header) with user-assigned managed identity federated credentials.
3. **No IDs in the bundle & strict CSP:** `default-src 'self'; connect-src 'self'; ...`, zero third-party fetches, zero estate data in `localStorage` or Cache API.
4. **Layer 0 isolation:** Plan cards, gate results, kill-switch position, and spend numbers are rendered deterministically from structured data—never written or altered by a model or mode.
5. **Server-derived `hard_steady` (`F2`):** The server derives `approval-pending`, `kill-switch`, `security-finding`, `spend-alert`, and `gate-refused` on every turn from the plan store, switch flag, and open findings for this operator, across all modes (`events = server_events | client_events`). Client-reported screen events can add `plan-shown` or `destructive-step`, never remove a server event.
6. **Single model client via `agent_api` (`F3`):** `avd-assistant` holds **no** model credentials and imports **no** Anthropic or Foundry SDK (enforced by a CI grep check). Stage 3 calls `agent_api` with `tools: none`, so `gateway/` enforces the kill-switch check (R21), Prompt Shields scan on operator text and every retrieved note/fact (R26), token meter, and `injection.suspected` / `decoy.tripped` counters.

---

## 4. The Four Interactive Modes

The operator selects a mode in the **composer bar** (default: **Chief of Staff**) and can switch modes mid-conversation. Each turn records `mode` (`chief_of_staff | red_team | guru | buddy`).

| Mode | Role & Lens | Deterministic Stage 1 Backend (0 Tokens) | Output Structure & Guardrails | Aside / Humor |
| :--- | :--- | :--- | :--- | :--- |
| **Chief of Staff** *(default)* | Triage, prioritize, coordinate | `GET /api/status` ranked by **`governance/assistant-triage.yml`** (protected): switch != `run` -> plans awaiting operator -> spend alerts -> overdue teardowns -> findings by severity -> oldest open item (`F13`) | Bottom line first -> Top items needing attention -> Deterministic next step (Stage 3 text never sits in the "next step" slot and is labeled `AI synthesis`) | Never (`F16`) |
| **Red Team** | Challenge assumptions, surface risk | Static audit against `governance/policy.yml` + active plan checks (missing rollback, broad blast radius, unpriced spend, gate failures). Available on pending plans in `hard_steady` | **No pass state (`F4`).** Fixed footer: *"These N checks found nothing. This is not a gate result."* + Layer 0 gate results & plan hash. Stage 3 output is structured (`risks[]`, `assumptions[]`, `questions[]`) with no free-text summary | Never (`F16`) |
| **Guru** | Deep technical & architectural teacher | Read-only SQLite FTS5 index built from **pinned commit SHAs** of `avd-second-brain` and `avd-assess` in **`governance/assistant-index.yml`** (protected; build fails if pins move without a PR) (`F1`) | Direct answer -> Why it works -> Primary source citations (`0 tokens · brain @<sha7>`). **Snippet gate (`F1`):** Code blocks render as runnable/copyable **only** if `snippet_sha256` is in `catalog/snippets/` (protected); all others render as quoted text labeled *"unverified, not for pasting"* with no copy button. Learn links show full host before opening | Never (`F16`) |
| **Buddy** | Warm, dry-wit companion | Curated onboarding, navigation, and plain-English status templates (same snippet/link rules as Guru) | Answer first -> Brief why + link -> Optional `aside` | Only in `light` (or invited `soft_steady`); never in `hard_steady` (`F2`, `F16`) |

### Mode & Verdict Guardrails
- **Code-enforced `aside` drop (`F16`):** Before `voice_check.check_reply` runs, the server deletes `aside` unless `mode == "buddy"`, logging any drop by finding name.
- **Verdict-phrase filter (`F4`):** A protected list in `governance/assistant-voice.yml` (`safe`, `approved`, `approve it`, `ok to approve`, `no issues`, `looks good`, `go ahead`) withholds any matching Stage 3 output across all modes.
- **Mixed-mode history (`F14`):** Window entries are tagged with `mode`; Stage 3 receives rendered answers prefixed by mode name, and protected voice instructions specify that prior Red Team entries are questions, not evidence.

---

## 5. Near-$0 Cost Architecture: 4-Stage Deterministic-First Router

Every `POST /api/turns` request passes through four stages in order and stops as soon as a zero-token stage satisfies it:

### Stage 0 — Scope Gate (`0 tokens`, `F12`)
- **Context-aware:** Never refuses when an in-scope turn occurred in the last N turns or a plan/finding is open.
- **Positive off-topic match only:** Refuses only on positive match to an off-topic class (non-AVD code, creative writing, trivia, banned-topic joke request) or input > `1,000` chars. Everything else falls through to Stage 1's safe fallback.
- **Vendor-neutral referral (`F12`):** *"I only cover this estate and its knowledge base. For general questions use a general-purpose assistant, and don't paste estate names or IDs into it."*

### Stage 1 — Mode-Specific Mechanical Lookup (`0 tokens`)
- Executes the mode's deterministic handler (`Chief of Staff` triage, `Red Team` static plan/policy audit, `Guru` pinned FTS5 lookup, `Buddy` curated templates).
- Returns immediately with a visible source badge (e.g. `0 tokens · brain @<sha7>` or `0 tokens · rule-based`).

### Stage 2 — Fact Retrieval + Explicit Synthesis Opt-In (`0 tokens` default, `F5`)
- **No retrieval, no synthesis (`F5`):** If neither `C11`/`C21` facts nor the pinned brain index return a match, the turn ends with a `0 tokens` *"nothing in the estate or the brain matched"* card and two Stage-1-answerable suggestions. Stage 3 is never called on empty retrieval.
- When Stage 2 finds partial matches, it renders the **retrieved facts/notes directly (`0 tokens`, capped at 6 KB with a "more" fetch link)** and offers an explicit **"Synthesize with AI"** button (`auto_synthesis: off` by default, protected).

### Stage 3 — Metered LLM Fallback (`agent_api` with `tools: none`, `F3`, `F5`, `F10`, `F11`)
Invoked **only** when:
1. Stage 0 passed and Stage 2 produced at least one retrieved fact or note,
2. The operator explicitly tapped **"Synthesize with AI"** (or `auto_synthesis` is enabled for that mode),
3. Kill switch permits agents (`run` or `halt-actions`), and
4. The per-operator, per-day **Assistant sub-cap** in `governance/model-prices.yml` approves the reservation.

- **Isolated budget (`F5`):** Hitting the Assistant daily sub-cap pauses Stage 3 (*"AI synthesis is paused until tomorrow"*) and **never** trips the swarm's `halt-agents` watchdog.
- **Server-pinned model & effort (`F10`):** `governance/model-prices.yml` (protected) maps each mode to its `model_id`, `effort`, `max_tokens`, and daily sub-cap. Requests containing client-supplied `model` or `effort` fields are rejected with `400`.
- **Prompt caching off by default (`F10`):** Remains disabled until evals demonstrate a `> 50%` `cache_read_input_tokens` hit rate for a given mode.
- **Bounded sliding window (`F11`):** Holds the last 6–8 turns of **operator text and rendered answers only**. Retrieved bodies (capped at 6 KB), `aside` text, Red Team assumption lists, and Stage 1 cards are excluded from the window.

---

## 6. Hosting, Cold-Start Resilience & Azure Cost Controls

- **Ingress & Auth (`F7`):**
  - `avd-assistant` Container App: **external ingress**, Easy Auth (`Require authentication`), `allowInsecure: false`, token store disabled.
  - `agent_api` Container App: **internal ingress**, Entra app auth, accepts `avd-assistant`'s managed identity only.
  - Core deploys no custom VNet, so no NAT Gateway is deployed.
- **Idempotency & Cold-Start Handling (`F6`):**
  - Client generates an `idempotencyKey` per turn and sends it on `POST /api/turns` (missing key -> `400`). Server keys the turn and token reservation by `idempotencyKey`, returning the existing `turnId` on retry/reload.
  - `POST /api/turns` returns `202` with `turnId` within 1 second before Stage 1–3 work; any `agent_api` cold start lands on the short-poll (`GET /api/turns/{id}?after=n`).
  - Read-only FTS5 index is baked into the GHCR image for sub-millisecond startup.
- **Idle-Tab Scale-to-Zero Guard (`F9`):**
  - Client polls **only** while a turn is running. Status refreshes on `visibilitychange` to visible and explicit tap; a tab idle for 10 minutes stops all requests and shows *"paused, tap to refresh"*.
  - Scale rule: `http` (`concurrentRequests: 10`, `minReplicas: 0`), plus a $1 resource group budget alert.
- **Logging & Observability (`F8`):**
  - `avd-assistant` Container Apps environment uses `--logs-destination none` in core (`az containerapp logs show` for live debugging); optional system logs use Azure Monitor diagnostic settings with a 90% budget alert (never the deprecated HTTP Data Collector API or daily cap alone).
  - Application Insights is disabled; gateway injection/decoy/meter events go to the observer's immutable blob store (amending `spec.md` §3 in the implementation PR).
- **Kill Switch & Retention (`F15`, `F17`, `F18`):**
  - At `halt-all`, remove the stale static-host notice from `buddy-app.md` §3; the app shows *"avd-assistant can't reach its service"* and switches to the offline view (never served from the public demo origin).
  - Server-side turn/history store enforces **90-day retention** and a per-operator size cap, covered by the leak test.
  - False-positive stress phrases (`"scroll down"`, `"help me understand"`, `"I lost the link"`) can be added to `stress_exceptions` in `governance/assistant-voice.yml` by PR after the first sample read.
