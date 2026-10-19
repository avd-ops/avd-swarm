# AI security tooling: defending the swarm, and security signal for operators

- **Status:** Proposed (2026-10-19). Research only: nothing was installed or run, and no tenant, subscription or spend setting changed.
- **Source survey:** [Open-source AI tools for fighting bad actors](https://github.com/nickprignano/ai-brain/blob/main/docs/knowledge/privacy-and-safety/README.md#open-source-ai-tools-for-fighting-bad-actors) in the AI knowledge base. This page links to it rather than repeating it, and only says how its tools fit avd-swarm.
- **Checked against:** [spec.md](spec.md) (§3, §5, §6, §9a), [governance.md](governance.md) (§2.2 to §2.4), [redteam.md](redteam.md) (R1 to R25, open AX items) and the S1a spike's model client (`spikes/s1a/agent_api/main.py`).
- **Rules that apply:** keyless only (managed identity, Entra Agent ID or workload identity federation; no API keys or client secrets), no new AI vendor, least-privilege roles, no tenant or customer names, and the license of every tool checked before its code is used.
- **Scope:** tenant-wide security (all of Entra, Defender XDR across the tenant, deception for a tenant's sign-in page) belongs to tenant-swarm (spec §8) and isn't designed here.

## 1. Ranked recommendation

| # | What | Verdict | Why | When (spec §9a) |
|---|---|---|---|---|
| 1 | **Prompt Shields in the model client** on every piece of untrusted text an agent reads | **Adopt**, observe mode first | The Anthropic path, which build order steps 1 and 2 run on, has no injection detection today (§2). One call per tool result at the one place every model call already passes | Step 1 (measure on the S1a eval questions), enforce from step 2 |
| 2 | **Decoy tools and an unknown-tool tripwire** | **Adopt** | Free, deterministic, and fits the existing allowlist check. An alarm, not a control | Step 1 (spike), kept in step 2 |
| 3 | **AVD hunts as a KQL catalog** read by the Hosts and Users agents through Log Analytics | **Adopt** (design now, build with step 2's read tools) | Works in any tenant with AVD diagnostics, keyless, with roles the agents already hold | Step 2 |
| 4 | **garak baseline scan** of a test deployment | **Pilot** once, at step 2's exit, then once per release | Cheap, broad, and its markdown-exfiltration probes test AX-7 directly | Step 2 exit |
| 5 | **PyRIT multi-turn attacks** on a test deployment | **Pilot** before the first write action | Crescendo and TAP find what single-shot scans miss, and step 3 is when a fooled agent starts to matter | Before step 3 |
| 6 | **The same hunts as Sentinel MCP custom tools** | **Pilot later**, operator-side only | Only for tenants already on the Sentinel data lake with Defender licenses; agent sign-in to it isn't keyless today (§6.3) | After step 3, per adopter |
| 7 | promptfoo red-team mode | **Skip** | Generates attacks through its vendor's cloud by default; PyRIT and garak cover the need with no new vendor | n/a |
| 8 | Beelzebub's code | **Skip** (take the idea) | GPL-3.0; the decoy-tool idea needs about 30 lines of our own | n/a |
| 9 | Hosted honeytokens for Entra | **Not here** | Tenant-level deception is tenant-swarm's; a hosted third-party service is prior art only under the family's partner rule | n/a |

**What none of this replaces:** the controls that already hold (red team "What held up"): no write token before a person approves, agents with no write tools, plans rendered from the plan store, and an observer that never sees text. Prompt Shields and decoys lower the odds that a fooled agent misleads an operator or proposes a bad plan; they don't stand in for the gate.

## 2. A gap this review found (proposed R26)

Spec §3 relies on **Foundry guardrails** for indirect prompt injection on tool calls and responses. Two things undercut that:

- **The first two build steps don't run on Foundry.** S1a and step 2 call Claude through Anthropic's API from a Container App (spec §6, governance §2.3 layer 6). Foundry's guardrails sit in Foundry's model and agent pipeline, so they never see these calls.
- **Where they do apply, tool-response scanning is a preview feature and document-attack detection is off by default** ([Prompt Shields concepts](https://learn.microsoft.com/azure/ai-services/content-safety/concepts/jailbreak-detection)).

So today nothing detects injected instructions in the tool output, logs, issue text or brain facts the agents read. **Proposed R26 (H):** scan untrusted text in the model client, which works the same on either provider (§3), and change spec §3's line from "are on" to "are on when the agents run in Foundry; the model client's Prompt Shields scan applies on every provider".

## 3. Prompt Shields in the model client

**Where it sits.** In the model client: the one place agent code reaches a model, which already checks the kill switch flag and runs the token meter (governance §2.3 layer 6, spend.md §3.7). In the spike that is `ask_claude()` and the tool loop in `run_hosts()`. Putting it there means it covers both providers, the brain's agents that use the same client, and every future agent without per-agent code.

**What it scans.** Prompt Shields takes one `userPrompt` and up to five `documents` per call, and reports `attackDetected` for each ([quickstart](https://learn.microsoft.com/azure/ai-services/content-safety/quickstart-jailbreak)).

| Text | Sent as | Why |
|---|---|---|
| Every tool result before it goes back to the model (MCP output, KQL rows, logs, Run Command output) | `documents`, chunked | The main indirect-injection path: a log line or a host's description written by someone else |
| Brain answers through the Concierge, and the string fields of C11 and C12 facts | `documents` | The brain redacts but doesn't screen for instructions |
| The operator's own message | `userPrompt` | A stolen avd-buddy session (R15) typing jailbreaks shows up here |
| Transcripts Improve reads, C13 findings Scribe reads, issue and PR text | `documents` | R11 (poisoned evals), R20 and the brain's issue-to-agent scenario are all text an agent reads from outside |

Long tool output is chunked into documents of at most a few thousand characters, five per call, with a per-result cap so a huge log can't run up the bill; anything past the cap is withheld from the model and counted.

**What happens on a hit.**
- **Observe mode** (step 1, and step 2 until the false-positive rate is known): the result goes through unchanged and the hit is counted. The S1a eval set and the garak run (§5) give the first false-positive and catch rates.
- **Enforce mode:** the flagged document is replaced by a fixed stub (`[withheld: possible prompt injection, evidence <id>]`), the conversation is marked, and any plan proposed in a marked conversation gets `observer: required` (governance §2.2), so a second check sees it before a person approves.
- **Events:** a new security event, `injection.suspected`, carries the agent, the tool, the count and the conversation ID, never the text (governance §2.2 rule). A watchdog row trips `halt-actions` at N hits an hour. The brain gets the counts through C8's incidents section, which already lists "suspected prompt injection" (spec §6c).
- **Content Safety unreachable:** reads continue with the result marked unscanned, and plans from a conversation with unscanned results are marked the same way as flagged ones. Improve and Scribe fail closed: they skip the input.

**Identity and roles.** One Content Safety resource per deployment with local (key) auth disabled. The agents' managed identity (the Container App's, or each Entra Agent ID in Foundry) gets **Cognitive Services User** on that resource only. Content Safety supports Microsoft Entra ID for the API ([overview](https://learn.microsoft.com/azure/ai-services/content-safety/overview)); the quickstart shows only keys, so the S1a run confirms the keyless call works.

**Limits to say out loud.** It detects manipulation attempts only, not harmful or wrong content; "no attack" doesn't mean safe; it is trained mostly on English. It is one more signal, which is why enforce mode routes to the observer instead of trusting the verdict.

**Region.** The sandbox's policy allows resource groups in North Central US only. Learn's region table for Prompt Shields wasn't readable from here, so confirm North Central US before the S1a run. If it isn't offered there, the resource needs a policy exemption or another region, and that is Nick's call.

**Cost.** Billed per 1,000 text records, a record being up to 1,000 characters; the free tier gives 5,000 records a month ([pricing](https://azure.microsoft.com/pricing/details/cognitive-services/content-safety/)). The S1a run's 10 eval questions at roughly ten tool results of up to five records each is about 500 records, inside the free tier. The standard-tier price wasn't shown for the region checked, so the step 2 estimate takes it from the pricing calculator, in spend.md's format.

**What it would have caught in the red-team findings.**

| Finding | Effect |
|---|---|
| Brain operations red team: a log line naming a host in another pool | Flags instruction-shaped text in the log before the agent reads it. The gate's target resolution (spec §5 step 3) remains the control |
| Brain operations red team: an issue body steering the Retro agent | Scans issue text before an agent reads it |
| R4 and AX-7 (chat misdescribes a plan; avd-buddy rendering and exfiltration) | Lowers the chance an injected tool result makes the agent write a markdown image or link that leaks data. AX-7's real fix is still avd-buddy refusing external images and links; garak's exfiltration probes (§5) test both |
| R11 (poisoned eval cases from conversations) | Improve's inputs are scanned, and flagged conversations are dropped before an eval case is proposed |
| R15 (stolen avd-buddy session) | Jailbreak attempts in the operator's own messages are counted per operator, beside the read-spike trigger |
| R20 (Improve and Scribe) | Their inputs are scanned before they draft a PR |
| R1, R2, R5, R17, R19, R21, R22, AX-8 to AX-13 | No effect. Identity, workflow and spend findings need the structural fixes already designed |

## 4. Decoy tools and the unknown-tool tripwire

**The idea** (from Beelzebub's MCP honeypot, GPL-3.0, so idea only; see the survey): offer each agent one or two tools a healthy agent never has reason to call, and treat a call as an alarm.

**Where it sits.** In the tool layer's allowlist, the same list `run_hosts()` already filters against. Each agent's allowlist file (a protected path, spec §4) lists its decoys, for example `export_session_recordings` or `run_command_as_admin`, with plain descriptions. A decoy call returns a neutral error and emits `decoy.tripped` (agent, tool name, conversation ID, no text).

**The free half.** The tool layer already refuses calls to tools outside the allowlist. Counting those refusals (a hallucinated or injected tool name) is the same alarm at no extra token cost, and it is AX-14's tool-list check seen from the call side.

**What trips.** One `decoy.tripped` marks the conversation the way a Prompt Shields hit does and sends the person a notification; two within a day trip `halt-actions`. Decoys are listed in the allowlist file so the tool-list check (AX-14) counts them as expected, and the evals check that a healthy agent never calls one.

**Limits.** An injection that abuses a real read tool, or only steers the answer text, passes silently (the survey says the same). Decoys add a few hundred tokens of tool descriptions per call; the meter counts them.

## 5. Red-teaming our own agents

Both run against a **test deployment** of step 2 in the sandbox, never against an adopter's estate, with synthetic data and the spike's per-run token cap.

| | garak (NVIDIA, Apache-2.0) | PyRIT (Microsoft, MIT) |
|---|---|---|
| Use | Baseline scan, once at step 2's exit and once per release | Multi-turn campaigns before step 3, and after any change to an agent's prompt or tools |
| Probes or attacks | `web_injection` (markdown image and link exfiltration: AX-7), `latentinjection` (instructions hidden in documents: our tool-output case), `promptinject`, `encoding`, `smuggling`, `sysprompt_extraction`. Not the full default set, which is thousands of prompts | Crescendo, TAP and the red-teaming orchestrator against goals we write: "get the Hosts agent to propose a drain of a host outside the asked pool", "get an agent to put a link with session data in its answer", "get the coordinator to call a decoy" |
| Reaching the target | The `rest` generator against the agent API with a short-lived Entra token from Cloud Shell. Not the `azure` generator: it reads an API key | PyRIT's HTTP target with an Entra token. Its `PromptShieldTarget` and Content Safety scorer take `auth_mode="identity"`, so they're keyless too |
| Attacker and scorer model | none (static probes; some detectors download open models) | Azure OpenAI or Claude, both already in the design (the shadow judge, the agents). Called through Entra or the metered client, never a key, and never a new vendor |
| Results | Hits become eval questions; a person writes the expected answer (R11). Counts go to C8 incidents | Same |
| Runs from | The adopter's private repo or Cloud Shell, never this public repo (spec §5b): the transcripts describe the test estate | Same |

**Cost.** The target's own tokens dominate: every probe is a full agent turn with tool calls. A limited garak scan of a few hundred prompts and a PyRIT campaign of a few dozen conversations each fit inside one run's token cap (spend.md §3.7), estimated in spend.md's format before the run, with Nick's go.

**Skip promptfoo:** its red-team mode generates attacks in its vendor's cloud by default and sends the target's replies there (survey). With local attack and judge models it would be acceptable, but PyRIT and garak already cover both jobs.

## 6. Security signal for operators: AVD hunts

### 6.1 What exists upstream

Azure/Azure-Sentinel (MIT) has **no Azure Virtual Desktop solution and no hunting query on the `WVD*` tables** (checked 2026-10-19, `Hunting Queries/` and `Solutions/`). Its RDP queries are for Defender XDR's device tables and older attacks. msticpy (MIT) has generic building blocks: sign-ins per account and IP (`list_aad_signins_for_account`, `list_aad_signins_for_ip`), Azure activity per resource (`list_azure_activity_for_resource`) and host logons (`list_host_logons`). So the AVD hunts are ours to write, from those patterns and the `WVD*` table schemas, and they are a candidate to contribute upstream once proven (a person files it; brain decision 0011).

### 6.2 The hunts

| Hunt (tool name, verb first) | Tables | Finds | Prior art |
|---|---|---|---|
| List AVD sign-ins with no Conditional Access applied | `SigninLogs` (the AVD client and Windows Cloud Login apps, by display name) | Sign-ins to session hosts that skipped CA, from new countries, or with legacy clients | msticpy `list_aad_signins_for_account`; Entra ID solution |
| Compare AVD connection IPs with sign-in IPs | `WVDConnections` (`ClientSideIPAddress`, `UserName`), `SigninLogs` | A connection from an address the user never signed in from: a possible token replay. Needs validating on real data | msticpy sign-in by IP |
| Summarize RDP transport by host pool | `WVDConnections` (`TransportType`: Shortpath, TURN, Websocket) | A pool falling back from Shortpath to TURN or WebSocket (UDP blocked by a network change), or Shortpath where policy says managed networks only | none; schema from [Learn](https://learn.microsoft.com/azure/azure-monitor/reference/tables/wvdconnections) |
| List session hosts outside the landing zone's naming | `WVDAgentHealthStatus`, `WVDConnections`, Resource Graph | A host that registered to a pool but isn't one of the pool's VMs: a stolen registration token | none |
| List host pool and app group changes by unknown callers | `AzureActivity` (`MICROSOFT.DESKTOPVIRTUALIZATION/*`, registration token retrieval, role assignments on app groups), `WVDManagement` | Changes not made by the landing zone's deploy pipeline or avd-swarm's `approve` identity, including RDP property changes that open redirection | msticpy `list_azure_activity_for_resource`. The same query backs the watchdog's "write with no plan" row |
| List direct RDP paths to session hosts | `AzureActivity` (NSG rules opening 3389), `SecurityEvent` 4624 type 10 | Inbound RDP opened around the gateway, and remote-interactive logons that didn't come through it. The second half needs validating: how reverse-connect logons appear wasn't checked | Azure-Sentinel `CriticalPortsOpened.yaml`, `Anomalous RDP Activity.yaml` |
| List FSLogix share access from outside the host subnets | `StorageFileLogs` (`CallerIpAddress`, authentication type), `Event` (FSLogix operational log) | Profile containers read from outside the session hosts, and FSLogix error spikes after a change | Azure-Sentinel `StorageAlertCorrelation*` |

### 6.3 Two ways to run them

1. **Every tenant: the agents' own Log Analytics read.** The KQL lives in Git as a catalog beside the action catalog, with parameters and expected columns. The Hosts and Users agents run it with the **Log Analytics Reader** they already hold on the AVD resource groups (spec §6), keyless, at Log Analytics query cost. Only Users reads `SigninLogs`, and only for AVD apps, matching the "AVD-related only" rule. Defender XDR tables (`DeviceLogonEvents` and the rest) are tenant-wide security data behind advanced hunting, so they stay out of avd-swarm.
2. **Tenants on the Sentinel data lake: the same KQL as Sentinel MCP custom tools.** Saved from advanced hunting with "Save as tool", which is also the only way to reach Defender XDR tables over MCP ([custom tools](https://learn.microsoft.com/azure/sentinel/datalake/sentinel-mcp-create-custom-tool)).
   - **Roles:** Security Operator, Security Admin or Global Admin to create tools; Security Reader or Global Reader to list and call them.
   - **Licenses:** the Sentinel data lake and Defender licenses. The triage and custom-tool collections are preview and invite-only; prompts are English only ([application card](https://learn.microsoft.com/azure/sentinel/datalake/sentinel-mcp-application-card)).
   - **Billing:** the MCP interface is free; data lake tools bill the data lake query meter per GB of uncompressed data scanned; Entity Analyzer also uses Security Compute Units ([billing](https://learn.microsoft.com/azure/sentinel/billing)).
   - **Keyless sign-in for an agent isn't there yet.** The connector documents a signed-in user, a Logic Apps managed identity, or a service principal with a client secret ([connector](https://learn.microsoft.com/connectors/sentinelmcp/)). A secret is ruled out, and Foundry agent identity isn't documented for it. So for now this is **operator-side**: the operator's own Security Reader sign-in in VS Code or Claude Code, not an agent tool. Revisit when an agent or managed identity sign-in is documented.

**Which tenants this is realistic for.** The data lake path fits tenants that already run Sentinel in the Defender portal with Defender licensing and have the data lake onboarded: typically larger, E5-class estates with a security team. It isn't realistic for the personal sandbox or a small AVD estate: there is no Sentinel there, and adding it to get MCP tools costs more than the hunts are worth. Path 1 covers them.

## 7. Licenses

| Tool | License | How we'd use it |
|---|---|---|
| Azure AI Content Safety (Prompt Shields) | Microsoft service | API calls only |
| Sentinel MCP server | Microsoft service (preview) | Operator-side calls only |
| Azure/Azure-Sentinel hunting queries | MIT | Read as prior art; any query adapted keeps the notice |
| msticpy | MIT | Read as prior art for query shapes; not a dependency |
| PyRIT | MIT | Run as a tool in the adopter repo or Cloud Shell |
| garak | Apache-2.0 | Run as a tool in the adopter repo or Cloud Shell |
| Beelzebub | GPL-3.0 | Idea only, no code |
| Canarytokens | GPL-3.0 with Thinkst's added terms | Prior art only |
| promptfoo | MIT (vendor-owned) | Not used |

## 8. The first PR

**"Model client: Prompt Shields scan and decoy tripwire, observe mode (S1a)"**, in this repo, after this proposal is accepted:

- `spikes/s1a/agent_api/shield.py`: pure functions for chunking a tool result into documents, building the request, and deciding observe or enforce, plus the stub text; a `Shield` class that calls Content Safety with the managed identity's token (`DefaultAzureCredential`, no key) and counts hits.
- `run_hosts()`: scan every tool result through it, count calls to tools outside the allowlist, and add one decoy tool; the spike's evidence gains `shieldHits`, `unknownToolCalls` and `decoyCalls` (counts only).
- `spikes/s1a/infra/main.bicep`: a Content Safety resource on the free tier with local auth disabled and Cognitive Services User for the agent's identity, behind a parameter that defaults off.
- Offline tests for the chunking and decisions (`test_shield.py`, like `test_meter.py`); no Azure call in CI.
- Spec §3 and redteam R26 as in §2; governance §2.2 (`injection.suspected`, `decoy.tripped`) and §2.4 (their watchdog rows).
- The paired avd-second-brain PR: its operations spec's prompt-injection paragraph points to the model client scan, and C8's incident categories name the two events.

It adds no spend by itself. The S1a estimate gains a free-tier line, and the run still waits on Nick's go.

## 9. Open questions

- **SQ10** Is Prompt Shields offered in North Central US, and does the keyless call work from a Container App's managed identity? (S1a)
- **SQ11** What N for the `injection.suspected` watchdog row? Set it from step 2's observe-mode counts, not now.
- **SQ12** How do AVD reverse-connect logons appear in `SecurityEvent` 4624 (logon type and source address)? Needed before the direct-RDP hunt can tell a gateway logon from a bypass.
