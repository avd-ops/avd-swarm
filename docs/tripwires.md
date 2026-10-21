# Estate tripwires: a free kit (spec)

- **Status:** Draft spec, 2026-10-21, red-teamed the same day (§9). Nothing here is built, and nothing that bills is turned on without the owner's go on a written estimate.
- **Part of:** [security-tooling.md](security-tooling.md) (decoy tools, §4, are the agent-side half of the same idea) and [governance.md](governance.md) §2.2 to §2.4 (observers, kill switch, watchdog).
- **Pairs with:** avd-second-brain's operations KB page (the facts this spec rests on) and, at build time, its C8 incident categories.
- **Scope:** one AVD estate: the resources an avd-landing-zone deployment owns. Directory decoys (a canary user, a decoy app credential) are tenant-level and out of scope here, as security-tooling §1 row 9 already says.

## 1. What and why

A tripwire is something nobody healthy ever touches, wired so that a touch raises an alarm. It is an **alarm, not a control**: it stops nothing, and it says nothing about an attacker who never touches it. What it buys is a high-signal alert at almost no cost, because the normal rate is zero.

**What it catches, honestly.** Tooling that dumps every secret or asks for every key, and opportunists who reach for credentials. It does not catch a careful attacker who has read this public repo, listed the vault's secret names through ARM (which logs no `SecretGet`) and skipped the decoy. §3 makes the decoy blend in and watches the real break-glass secret too, which narrows that gap; it doesn't close it.

Honeypot VMs (a fake SSH or SMB server waiting for scans) are **not** what this kit is. They bill by the hour, need patching, and an AVD estate with no public IPs gets no scans to catch. The kit uses what the landing zone already has: its Key Vault, its storage account, its Log Analytics workspace and its Activity Log.

Nick's rule for where it lives (2026-10-21): **the free option is avd-swarm and the brain**, with only very basic plumbing in avd-landing-zone and avd-current-state. That split is §5.

## 2. Prior art (checked 2026-10-21)

| Tool | What it is | License | Verdict |
|---|---|---|---|
| [Microsoft Sentinel Deception (Honey Tokens)](https://learn.microsoft.com/azure/sentinel/monitor-key-vault-honeytokens) | Decoy keys and secrets planted in Key Vault, alerting through Sentinel analytics rules; a Function App plants them | MIT ([Azure-Sentinel](https://github.com/Azure/Azure-Sentinel/tree/master/Solutions/HoneyTokens)); Learn says it is offered "in a community supported model", not as a supported product | **Closest prior art, not adopted.** It needs Sentinel (billed per GB ingested) and a Function App with write access to the vault. We take the idea; an adopter already on Sentinel can run it instead of §4.2 |
| Canarytokens (Thinkst) | Hosted honeytokens: cloud credentials, documents, URLs, an Entra sign-in page beacon | As security-tooling §1 row 9; a hosted service | **Prior art only** (partner rule; hosted third party). Its Entra CSS beacon is dying with custom CSS in company branding |
| OpenCanary (Thinkst) | A Python service honeypot (SSH, SMB, RDP, HTTP listeners) | BSD-3-Clause | **Prior art only.** Needs a VM that bills by the hour; nothing scans a private AVD subnet |
| Cowrie | SSH and Telnet honeypot | BSD-3-Clause | **Prior art only**, same reason |
| T-Pot (Deutsche Telekom) | A platform of twenty-odd honeypots with an Elastic stack | GPL-3.0 | **Prior art only.** Needs a large VM; the opposite of never leaving the meter running |
| Defender for Key Vault | Anomaly detection on vault access | Paid Defender plan | **Not needed.** An adopter who has it keeps it; the kit works without it |
| Beelzebub | MCP honeypot | GPL-3.0 | Already taken as an idea for decoy tools (security-tooling §4) |

The prior-art script (`scripts/kb/prior_art.py`) found nothing in the family's knowledge bases; its GitHub searches are blocked from this container, so the table came from Microsoft Learn and the projects' own pages.

## 3. The tripwires

Each is one bait and one detection. All are estate-scoped and none touches the directory.

| # | Tripwire | Bait | Signal | Valid only when |
|---|---|---|---|---|
| T1 | **Decoy secret** | One extra secret in the landing zone's Key Vault, named and tagged like the real ones, value never held by anyone | A **successful** `SecretGet` or `SecretBackup` on it (`ResultSignature == "OK"`) | Always |
| T1b | **Break-glass watch** | The real local-admin secret | A successful `SecretGet` on it outside a deploy or drill window | Always (its only scripted reader is the landing zone, at deploy time) |
| T2 | **Key asks on the profile storage account** | The account itself | `listKeys`, `regenerateKey`, `listAccountSas` or `listServiceSas` in the Activity Log, `status` `Succeeded` or `Failed` | Shared key access is off **and** no Recovery Services vault protects the share (vault-tier Azure Files backup needs key access, so its service calls `listKeys` legitimately) |
| T3 | **Tamper check** | The kit itself | A state check, not a write count (§4.1): every piece of the kit still present, enabled and wired | Always |

**Decoy naming.** The name is a `@secure()` parameter (secure parameters are kept out of deployment history, so Reader can't read it there), chosen by the operator from the estate's own naming style, and never written in a public file. `contentType` and tags copy the real secrets' style. The spec gives no example name on purpose.

**Who can read T1 legitimately.** No agent: no agent identity holds a Key Vault data role. Humans can: the landing zone grants the admins group Key Vault Secrets User on the vault, and the portal shows a secret's value on request. So the kit is only on when the admins know it is (§7 shows them), and a read denied by the vault's firewall (public network access is off) is a `tripwire.probe` notice, not a trip: it is logged with `IsAddressAuthorized` false, and it proves someone is trying from outside.

**False positives to know about (T2).** The Azure portal uses Shared Key for file shares by default, so an admin who browses the profile share in the portal calls `listKeys` and trips T2. With the kit on, admins don't browse the share in the portal; the drill shows this once so it is no surprise. The landing zone refuses T2 when the backup tier protects the share and deploys T1, T1b and T3 only; avd-current-state reports T2 as `incompatible`, never as wired.

**Not in the kit, and why:**
- **A decoy blob.** The profile account is `FileStorage` (premium file shares only), so it has no blob service. A blob decoy would need a new account and blob logging, more than the signal is worth.
- **A decoy file on the profile share.** Writing it needs a data-plane step over the private endpoint, and FSLogix, backup and endpoint protection scans all touch the share, so "never touched" is hard to promise. Revisit after a sandbox run shows what reads the share root (§8, Q5).
- **Directory decoys:** tenant-level, out of scope here.

## 4. Detection: the free path first

### 4.1 The free path: avd-swarm's watchdog

The watchdog (governance §2.4) already runs deterministic rules on a schedule. The kit adds one query and one state check, run hourly by default.

```kusto
// T1, T1b. Reads both table shapes; matches the exact secret path, never a prefix.
let names = dynamic(["<decoy-name>", "<break-glass-name>"]);  // from the private tripwire list, never a public file
let since = todatetime("<high-water mark>");                   // last processed ingestion time, from the run store
union isfuzzy=true
  (AzureDiagnostics | where ResourceProvider == "MICROSOFT.KEYVAULT"
     | project TimeGenerated, OperationName, Uri = requestUri_s, ResultSignature, Authorized = tostring(column_ifexists("isAddressAuthorized_b", ""))),
  (AZKVAuditLogs
     | project TimeGenerated, OperationName, Uri = tostring(RequestUri), ResultSignature, Authorized = tostring(IsAddressAuthorized))
| where ingestion_time() > since and TimeGenerated > ago(1d)
| where OperationName in ("SecretGet", "SecretBackup")
| extend Secret = extract(@"/secrets/([^/?]+)", 1, Uri), Ingested = ingestion_time()
| where Secret in (names)
| summarize Hits = count(), Mark = max(Ingested) by Secret, OperationName, ResultSignature, Authorized
```

- **Time window.** The high-water mark is on ingestion time, not event time, so a row that arrives late (Key Vault logs can arrive about ten minutes after the event) is still read once and never twice; the one-day floor only bounds the scan.
- **The tamper check (T3)** re-reads, through ARM, that the decoy secret exists, is enabled and has not gained a version since the last deploy; that the vault's diagnostic setting still sends the audit category to the workspace; that the T2 alert and its action group exist and are enabled; and that the watchdog itself last ran on time. Anything missing, disabled or unwired is a trip. Writes that leave it intact (a normal redeploy PUTs every resource again, and ARM logs a write each time) are only a `tripwire.changed` notice. No identity is exempt, the deploy principal included, because it is exactly the one an attacker wants.
- **What it emits:** `tripwire.tripped`, `tripwire.probe` or `tripwire.changed` (tripwire ID, operation, hit count). Never the caller's identity or IP: those stay in the workspace for the person.
- **Cost:** querying the Analytics tier is free; a Consumption Logic App recurrence with a handful of actions an hour costs cents a month. **Latency:** up to an hour plus ingestion. That is the price of free.
- **Which identity reads.** The switch's identity holds only take-away roles today (governance §2.3). Reading the landing zone's workspace is a new read role, so the build either adds Log Analytics Reader on that one workspace to §2.3 in writing, or runs the query from an agent identity that already has it and lets it only *recommend* a trip. Decided at build time with the owner (§8, Q7).

**T2 needs no query.** It is a free Activity Log alert. Its action group calls the switch's trip endpoint as a **Secure webhook**: the call is signed in as Azure Monitor's first-party webhook service principal against an app registration made for the endpoint, and the Consumption workflow's authorization policy accepts only that caller, with SAS keys off. (Managed-identity action groups exist only for some action types and would need a write role on the switch, which governance R5 forbids.) The endpoint takes **alert rule IDs listed in the private tripwire list** and derives the position from that list; the payload never chooses a position, and an unknown rule ID is refused even with a valid token. It dedupes by `correlationId`.

### 4.2 The optional path: a log search alert in the landing zone

For an estate that wants T1 paged within minutes, or runs the landing zone without avd-swarm: one more log search alert in `bicep/modules/monitoring.bicep`, carrying its own frequency (the module today applies five minutes to every row, so the row needs a `frequency` field), sent to the landing zone's action group. It needs `alertEmailAddresses`, or it alerts nobody; the landing zone refuses the switch without them.

- **Frequency:** 15 minutes. Log search alerts bill by evaluation frequency ([Learn](https://learn.microsoft.com/azure/azure-monitor/alerts/alerts-types#types-of-azure-monitor-alerts)); expected about $0.50 a month, **to be priced against exactly one retail meter by the landing zone's estimator before the owner's go**, never guessed.
- **Off by default.** It is the one recurring charge in the kit, so it needs the owner's go per estate.

## 5. Who does what

| Repo | Role | What changes |
|---|---|---|
| **avd-swarm** (this repo) | Responds | The watchdog query and tamper check (§4.1), the trip endpoint's rule list, the three events in governance §2.2, the rows in §2.4 (§6), the drill (§7). The tripwire list (names, alert rule names) lives in the adopter's **private** repo, keyed to the landing zone's base name, never resource IDs; this repo holds a placeholder |
| **avd-second-brain** | Knows | The facts and limits on its operations KB page; at build time, `tripwire.tripped`, `tripwire.probe` and `tripwire.changed` as C8 categories (counts only), changed with this repo as every C8 change is; a rule "tripwires present and wired" that its Tier 1 job reports from C11 facts |
| **avd-landing-zone** | Very basic plumbing, optional | `deployTripwires` (bool, **default false**): the decoy secret (name `@secure()`, value seeded once, so versions don't pile up and what-if stays clean), the vault's audit category pinned explicitly (today it comes from the AVM module's default, which a module bump could change), and T2's free alert unless the backup tier is on. `tripwireAlertMinutes` (int, **0 = off**) adds §4.2. Removal is explicit: turning the switch off doesn't delete anything in an incremental deployment, so cleanup deletes the decoy and the alerts by name; a purge-protected vault keeps the deleted decoy for its retention period, at no cost |
| **avd-current-state** | Very basic plumbing, read-only | One validator check: the decoy exists (read through ARM `Microsoft.KeyVault/vaults/secrets`, metadata only, which never logs a `SecretGet`), the diagnostic setting's categories include audit, the T2 alert is enabled with an action (or T2 is `incompatible`), and the date of the last passing drill. Reported as C11 counts and booleans only, never a secret name |

**The landing zone and avd-current-state stay self-contained.** With `deployTripwires` on and no avd-swarm, an estate gets T2 for free and T1 through §4.2 if the owner pays for it; avd-current-state checks both. avd-swarm adds the free hourly T1, T1b and T3 and the response.

## 6. Response

| Trigger | Kill switch position | Why |
|---|---|---|
| `tripwire.tripped` on T1, T1b or T2 | `halt-actions`, and page the person | Someone with real access is reaching for credentials. The swarm stops changing an estate it can no longer trust until a person looks; reads go on |
| T3: a piece of the kit missing, disabled or unwired | `halt-all`, and page the person | Silencing the alarm is a stronger signal than touching the bait |
| `tripwire.probe` (a firewall-denied read) or `tripwire.changed` (kit intact after a write) | none; the person is notified | |

These go into governance §2.4's table as two rows (the first two above). Only the person resets, as for every trip (§2.3). No agent can trip, reset or silence a tripwire. After a trip the agents don't read the audit rows; the person does, in the workspace.

## 7. Proving it works: the drill

A tripwire that has never fired is a guess. The drill is a Cloud Shell block a person runs, plus one step from inside the network:

1. Opens a drill window (logged, at most 30 minutes, sent to the observer as an event). It expects **exactly** one successful `SecretGet` on T1 and one `listKeys`, both by the identity that opened it. Any other hit, any T3 finding, or a second window within 24 hours trips as normal, so a hijacked session can't use a window as a bypass.
2. Reads T1 once **from a session host or another VNet-attached shell** (the vault refuses public network access, so a read from Cloud Shell is only a denied probe) and discards the value. Calls `listKeys` on the profile account once from Cloud Shell; it is a control-plane action, so it works with shared key off.
3. Waits for both signals, prints when each arrived, and shows the portal share-browse path as an expected T2 trip so admins know to avoid it.
4. Closes the window and writes the result (times, pass or fail) to the run store.

It runs once after the kit is turned on and after any change to the vault, the account, the workspace or the alerts.

## 8. Open questions for the owner

- **Q1** Is the line right: estate-scoped tripwires (Key Vault, storage keys, tamper check) free and public here, directory decoys never here?
- **Q2** §4.2's log search alert is the only recurring charge (about $0.50 a month per estate at 15 minutes, to be priced exactly). Offer it as an opt-in, or leave the landing zone at T2 and the swarm's hourly check?
- **Q3** Is `halt-actions` on a single T1, T1b or T2 trip right, or too eager for a solo-mode estate?
- **Q4** Hourly for the free check, or every 15 minutes (still cents in Logic App actions)?
- **Q5** A sandbox run, later and with its own estimate and go, to confirm what calls `listKeys` and `SecretGet` in a healthy estate (portal blades, backup, the Entra Kerberos path, the Az cmdlets our scripts use) and what reads the profile share root.
- **Q6** Build order: the landing zone and avd-current-state plumbing first (it works without the swarm), or after the swarm's step 2 watchdog exists?
- **Q7** Which identity runs §4.1's query: a new Log Analytics Reader role on the switch's identity, or an agent identity that only recommends a trip?

## 9. Red team (2026-10-21)

Twenty-two findings from a red-team pass, all folded in above. The ones that changed the design:

- A normal redeploy writes every kit resource again, so counting writes would trip `halt-all` at every deploy: T3 became a state check, with writes only a notice.
- The portal and vault-tier Azure Files backup both call `listKeys` legitimately: T2 is valid only with shared key off and no backup vault, and the landing zone refuses it otherwise.
- The decoy's name was readable from deployment history and the public example: it is a secure parameter, never written down publicly, and the real break-glass secret is watched too (T1b).
- Deleting the diagnostic setting, the alert or the watchdog silenced the kit unseen: the tamper check covers each of them.
- The query had no time window and matched name prefixes: a high-water mark and an exact path match.
- The trip endpoint would have let an alert payload choose a position: it now derives the position from a private list of rule IDs.
- A drill window was a 30-minute bypass, and a Cloud Shell read of the decoy only reaches the firewall: the window expects exact hits from one identity, and the read runs inside the network.
- Smaller fixes: the action group authenticates as a Secure webhook, not a managed identity; the paid alert needs its own frequency and an email list; firewall-denied reads are probes; removal is explicit.

Carried to the build, not fixed here: governance §2.2's sentence that the budget alert's action group calls `trip` "with its own managed identity" should say Secure webhook, in the same PR that builds the trip endpoint.
