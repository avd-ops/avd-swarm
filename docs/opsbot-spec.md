# `avd-opsbot` — Interactive Assistant & Zero-Token-First Architecture Spec

> **Personal research project — not for production.** Provided as is, without warranty; not affiliated with the author's employer, Liquidware or Microsoft.

- **Status:** Proposed (2026-11-17) — staged for Fable red-team review before implementation.
- **Supersedes / Evolves:** [`buddy-app.md`](buddy-app.md), [`buddy-voice.md`](buddy-voice.md) (renaming `avd-buddy` → `avd-opsbot` and demoting Buddy to one of four interactive modes).
- **Builds on:** [`decisions/0010-avd-buddy-container-app-hosting.md`](decisions/0010-avd-buddy-container-app-hosting.md), [`spend.md`](spend.md), [`redteam.md`](redteam.md).

---

## 1. Purpose & Core Shift

`avd-opsbot` (UI short name: **OpsBot**) replaces `avd-buddy` as the installable PWA front end and interactive assistant for `avd-swarm`.

Two core shifts drive this redesign:
1. **Multi-mode interactive assistant:** Instead of a single fixed persona, OpsBot provides four user-selectable lenses (**Chief of Staff**, **Red Team**, **Guru**, **Buddy**). **Buddy** becomes one mode rather than the product identity.
2. **Deterministic-first, LLM-as-last-resort:** OpsBot is laser-focused on mechanical `avd-ops` operations and organizational second-brain (`avd-second-brain`) queries. It answers as much as possible at **0 tokens** using deterministic code, local indexing, and structured plan/status data; rejects off-topic general chat (referring users to general-purpose assistants); and invokes an LLM **only** when synthesis or reasoning is genuinely warranted.

---

## 2. Identity, Branding & Icon

- **Product name:** `avd-opsbot` (UI title / PWA `short_name`: **OpsBot**).
- **Public static demo:** `opsbot.avd-ops.dev` (`?sim=1` offline fixtures, zero Azure spend).
- **Live adopter deployment:** Scale-to-zero Azure Container App (`minReplicas: 0`) with Entra ID Easy Auth and GHCR digest-pinned images per [decision 0010](decisions/0010-avd-buddy-container-app-hosting.md).
- **Icon (Shield + `><` Visor):**
  - Replaces the Clippy-style paperclip mascot in `assets/buddy/avd-buddy-icon.svg` (removing trademark/mascot baggage noted in `assets/buddy/REVIEW.md`).
  - Vector mark (`assets/opsbot/avd-opsbot-icon.svg`) using `avd-ops` Design System 3.0 tokens (`--brand` `#1ca998`, `--brand-ink` `#0d1015`): the `avd-ops` shield silhouette housing a dark geometric visor with `><` remote-session chevrons as the bot's eyes/prompt.
  - Built into the full PWA icon set (`192`, `512`, `maskable-512`, `monochrome-512`, `apple-touch-icon` `180`, `favicon.svg`, `favicon-32.png`, `avatar.svg`).

---

## 3. Security & Control-Plane Invariants (Unchanged)

All invariants from [`buddy-app.md`](buddy-app.md) §2 and [`buddy-voice.md`](buddy-voice.md) §1 remain mandatory:

1. **Front end only / no in-app approvals:** OpsBot never writes to Azure or approves plans. Every change is a plan in the plan store approved by a person in the GitHub `approve` environment.
2. **No tokens or secrets in the browser:** Entra ID Easy Auth (`HttpOnly` `AppServiceAuthSession` cookie + `X-MS-CLIENT-PRINCIPAL` header) with user-assigned managed identity federated credentials.
3. **No IDs in the bundle & strict CSP:** `default-src 'self'; connect-src 'self'; ...`, zero third-party fetches, zero estate data in `localStorage` or Cache API.
4. **Layer 0 isolation:** Plan cards, gate results, kill-switch position, and spend numbers are rendered deterministically from structured data—never written or altered by a model or mode.
5. **Code-enforced `hard_steady`:** When a plan card is on screen, kill switch != `run`, a security finding is active, or a destructive/data-loss step is referenced, `voice_check.py` forces `aside_allowed = False` regardless of the selected mode.

---

## 4. The Four Interactive Modes

The operator selects a mode in the **composer bar** (default: **Chief of Staff**) and can switch modes mid-conversation. Each turn records `mode` (`chief_of_staff | red_team | guru | buddy`).

| Mode | Role & Lens | Deterministic Stage 1 Backend (0 Tokens) | Output Structure | Aside / Humor |
| :--- | :--- | :--- | :--- | :--- |
| **Chief of Staff** *(default)* | Triage, prioritize, coordinate | `GET /api/status` (pending plans, kill switch, spend vs. cap, overdue runs, active `C11`/`C21` findings) | Bottom line first -> Top items needing attention -> Recommended next step | Never |
| **Red Team** | Challenge assumptions, surface risk | Static audit against `governance/policy.yml` + active plan checks (missing rollback, broad blast radius, unpriced spend, gate failures) | Checks run -> Risks & gaps surfaced -> Unverified assumptions (never emits a "safe to approve" verdict) | Never |
| **Guru** | Deep technical & architectural teacher | Read-only SQLite FTS5 index over `avd-second-brain` / `avd-assess` decisions, lessons, Cloud Shell snippets, and Microsoft Learn links | Direct answer -> Why it works this way -> Primary source citations | Never |
| **Buddy** | Warm, dry-wit companion | Curated onboarding, navigation, and plain-English status templates | Answer first -> Brief why + link -> Optional `aside` | Only in `light` (or invited `soft_steady`); never in `hard_steady` |

### Mode Guardrails
- **No authority illusion:** **Red Team** never outputs a "pass / safe / approved" verdict; it surfaces risks and explicitly notes that GitHub gates and human review are the authority. **Chief of Staff** prioritizes from structured data and labels any AI synthesis as `AI synthesis · verify against plan/facts`.
- **Distinct 0-token utility:** Switching modes changes *which deterministic Stage 1 data source* is queried—not just cosmetic phrasing.

---

## 5. Near-$0 Cost Architecture: 4-Stage Deterministic-First Router

Every `POST /api/turns` request passes through four stages in order and stops as soon as a zero-token stage satisfies it:

### Stage 0 — Scope Gate (`0 tokens`)
- Deterministic classifier using conversation context (e.g., open plan/finding), domain/entity patterns (AVD, Windows 365, Entra, Intune, M365, Azure landing zone, `avd-second-brain`, plans, spend, kill switch), and input length cap (e.g., `1,000` chars max).
- **Off-topic / general chat** (general coding, creative writing, trivia, non-AVD tasks):
  - **Never calls an LLM.**
  - Returns a crisp referral: *"I'm scoped strictly to your `avd-ops` estate and organizational second brain. For general questions, use your preferred general assistant (Claude, ChatGPT, Gemini, etc.)."*

### Stage 1 — Mode-Specific Mechanical Lookup (`0 tokens`)
- Executes the mode's deterministic handler (`Chief of Staff` status/triage, `Red Team` static policy/plan checks, `Guru` FTS5 second-brain lookup, `Buddy` onboarding/status templates).
- If the query is satisfied mechanically, returns immediately with a visible `0 tokens · local` badge.

### Stage 2 — Fact Retrieval + Optional Synthesis (`0 tokens` default)
- When `C11`/`C21` facts or `avd-second-brain` notes match partially without a single exact answer, OpsBot renders the **retrieved facts/notes directly (`0 tokens`)** and offers an explicit **"Synthesize with AI"** action (or escalates to Stage 3 only for explicitly analytical prompts when permitted).

### Stage 3 — Metered LLM Fallback (Last Resort)
Invoked **only** when:
1. Stage 0 passed (in-scope),
2. Stages 1–2 could not satisfy the query,
3. Kill switch permits agents (`run` or `halt-actions`), and
4. The metered token wrapper ([`spend.md`](spend.md) §3.7) approves the reservation against daily/monthly/run caps.

- **Read-only synthesis:** OpsBot's Stage 3 synthesis has **zero tool-calling or plan-proposing authority**; retrieved facts/notes are wrapped in untrusted-data delimiters to mitigate indirect prompt injection.
- **Model tiering & prompt caching:** Fast/low-cost model for **Buddy** / **Chief of Staff**; reasoning model only for **Red Team** / deep **Guru**. Static system/mode prefixes are structured for provider prompt caching.
- **Sliding context & output caps:** Bounded conversation window (last 6–8 turns, excluding `aside` text) + strict per-mode `max_tokens`.
- **Cost transparency:** Every turn displays its exact source badge (`0 tokens · local` vs. `~N tokens · <mode>`).

---

## 6. UI, Mobile Layout & Hosting Cost Controls

- **3-item mobile top bar preserved:** `OpsBot` icon + title (Settings), `Switch: <pos>` pill (Status), and `Menu` (History, Status & switch, Settings, Sign out).
- **Mode Switcher in the composer bar:** Keeps the 360px phone header uncluttered while allowing one-tap mode switching mid-thread.
- **Turn badges:** Assistant messages show a compact `.eyebrow` mode + cost badge (e.g., `RED TEAM · 0 tokens · local`).
- **Cold-start resilience:** Read-only FTS5 second-brain index is baked into the GHCR container image at build time (sub-millisecond startup, no database server). The UI shows a calm *"Waking OpsBot…"* state on scale-to-zero cold starts and disables duplicate Send taps.
- **Azure bill-trap guards:** Application Insights disabled; container logging set to `WARNING` with a Log Analytics daily cap; internal Container Apps ingress only (no NAT Gateway, no ACR, no Cosmos DB / Azure SQL).
