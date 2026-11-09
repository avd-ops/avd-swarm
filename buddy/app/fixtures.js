/* avd-buddy simulation and test fixtures (docs/buddy-app.md section 12).
   Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
   Uses contoso placeholder names only; holds no real tenant, subscription, object or client IDs. */

(function (global) {
  "use strict";

  const INITIAL_STATUS = {
    switchPosition: "run",
    switchMeaning: "Normal operation. Agents may read and propose plans; approved plans may run.",
    switchReason: "",
    tokenSpendUsd: 1.42,
    tokenCapUsd: 15.0,
    operator: "operator@contoso.com",
    simMode: true,
    waitingItems: [
      {
        id: "wait-plan-01",
        kind: "plan",
        title: "Plan plan-20261108-01 waiting for GitHub approval (hosts.drain-and-restart-agent)",
        threadId: "thread-drain-host-03"
      },
      {
        id: "wait-spend-01",
        kind: "spend",
        title: "Weekend idle check: 2 hosts in hp-contoso-sales ran 48h with 0 sessions",
        threadId: "thread-idle-hosts"
      }
    ],
    estate: {
      estate: "estate-a1f9c204",
      collectedAt: "2026-11-09T16:20:00Z",
      overallStatus: "pass",
      overallScorePct: 94,
      planes: [
        { id: "azure", name: "Azure (avd-azure)", status: "pass", scorePct: 96, pass: 27, warn: 1, fail: 0 },
        { id: "entra", name: "Entra ID (avd-entra)", status: "pass", scorePct: 95, pass: 18, warn: 1, fail: 0 },
        { id: "intune", name: "Intune (avd-intune)", status: "pass", scorePct: 92, pass: 14, warn: 1, fail: 0 },
        { id: "m365", name: "Microsoft 365 (avd-m365)", status: "pass", scorePct: 94, pass: 11, warn: 1, fail: 0 },
        { id: "power-platform", name: "Power Platform (avd-power-platform)", status: "pass", scorePct: 91, pass: 8, warn: 1, fail: 0 }
      ],
      hostPools: [
        { id: "hp-pooled-01", type: "Pooled", hostsTotal: 4, hostsAvailable: 4, activeSessions: 18, capacityPct: 56, autoscale: "enabled" }
      ],
      meterAudit: {
        runningSessionHosts: 4,
        deallocatedSessionHosts: 0,
        unattachedDisks: 0,
        orphanedNics: 0,
        publicIps: 0,
        idleWarnings: []
      },
      topFindings: [
        {
          checkId: "asm-files-zone-redundant",
          plane: "azure",
          outcome: "warn",
          severity: "medium",
          title: "Profile storage account uses LRS instead of ZRS",
          askPrompt: "Why is asm-files-zone-redundant warning on our profile storage account and how do we remediate it?"
        }
      ]
    }
  };

  const SWITCH_MEANINGS = {
    "run": "Normal operation. Agents may read and propose plans; approved plans may run.",
    "halt-actions": "Write path halted. Agents may still answer read-only questions, but no plan can be approved or executed.",
    "halt-agents": "Agents and MCP tool layer halted. Chat composer is off; use the Cloud Shell blocks below for read-only inspection or reset.",
    "halt-all": "avd-buddy's API and the tool layer's Container App are stopped."
  };

  const CLOUD_SHELL_RESET_BLOCK =
    "# In Azure Cloud Shell (requires operator break-glass role, governance section 2.3):\n" +
    "az resource update --resource-group rg-contoso-prod-swarm \\\n" +
    "  --name ca-avdswarm-agents --resource-type Microsoft.App/containerApps \\\n" +
    "  --set properties.configuration.activeRevisionsMode=Single\n" +
    "python3 scripts/governance/set_switch.py --position run --reason \"Verified clean state\"";

  const SAMPLE_PLAN = {
    planId: "plan-20261108-01",
    planHash: "a94f3c81e20b9d74c158a02e88b0194f",
    action: "hosts.drain-and-restart-agent",
    targets: ["sh-pooled-03 (hp-contoso-prod)"],
    expectedEffect: "New sessions stop routing to sh-pooled-03; 0 existing sessions affected; RDAgentBootLoader service restarts.",
    blastRadius: "1 of 6 session hosts in hp-contoso-prod (0 active user sessions)",
    why: "sh-pooled-03 missed 4 consecutive heartbeats in WVDAgentHealthStatus over the last 15 minutes while the VM power state is running and 0 sessions are active.",
    gateResults: [
      { gate: "Allowlist", outcome: "pass", text: "Pass: catalog action hosts.drain-and-restart-agent" },
      { gate: "Says why", outcome: "pass", text: "Pass: cites WVDAgentHealthStatus heartbeat gap and C15 fact ID" },
      { gate: "Live re-read", outcome: "pass", text: "Pass: 0 active sessions verified before proposal" },
      { gate: "Kill switch", outcome: "pass", text: "Pass: switch position is run" }
    ],
    rollback: "az desktopvirtualization sessionhost update -g rg-contoso-prod-avd --host-pool-name hp-contoso-prod -n sh-pooled-03 --allow-new-session true",
    expiry: "2026-11-08T21:30:00Z (expires in 30m)",
    approveUrl: "https://github.com/avd-ops/avd-swarm/actions"
  };

  const PRESET_TURNS = {
    "idle-hosts": {
      id: "turn-idle-01",
      threadId: "thread-idle-hosts",
      title: "Weekend idle hosts in Sales pool",
      operatorText: "Which hosts were idle all weekend?",
      mode: "light",
      events: [],
      answer:
        "Two hosts were idle all weekend: `sh-sales-01` and `sh-sales-02` in `hp-contoso-sales` had zero sessions from Friday 18:00 UTC to Monday 08:00 UTC. " +
        "Why it matters: a running VM bills compute every hour even with zero sessions, whereas a stopped (deallocated) VM stops compute billing while its OS disk keeps billing.",
      citations: [
        { id: "call-kql-01", label: "WVDAgentHealthStatus & WVDConnections (48h window)" },
        { id: "call-cost-01", label: "Cost Management: forecast_costs (rg-contoso-prod-avd)" }
      ],
      learnLinks: [
        {
          title: "Microsoft Learn: Azure VM states and billing",
          url: "https://learn.microsoft.com/azure/virtual-machines/states-billing"
        },
        {
          title: "Microsoft Learn: Autoscale for Azure Virtual Desktop session hosts",
          url: "https://learn.microsoft.com/azure/virtual-desktop/autoscale-scenarios"
        }
      ],
      aside: "Those two enjoyed a quieter weekend than most of us.",
      followUps: [
        "Drain host 3 and restart its agent",
        "The deploy failed and I don't know why",
        "Why is max sessions set to 8?"
      ],
      plan: null
    },
    "drain-host": {
      id: "turn-drain-01",
      threadId: "thread-drain-host-03",
      title: "Drain sh-pooled-03 and restart agent",
      operatorText: "Drain host 3 and restart its agent",
      mode: "hard-steady",
      events: ["plan-shown", "approval-pending"],
      answer:
        "I drafted plan `plan-20261108-01` to enable drain mode on `sh-pooled-03` (`hp-contoso-prod`) and restart its AVD agent service. " +
        "Approving runs this exact plan once in GitHub; the rollback command is listed on the card. If anything on the card looks wrong, do not approve—ask me and I will explain or draft a new plan.",
      citations: [
        { id: "call-mcp-02", label: "virtualdesktop_sessionhost_list (hp-contoso-prod)" },
        { id: "call-kql-02", label: "WVDAgentHealthStatus (sh-pooled-03, 0 active sessions)" }
      ],
      learnLinks: [
        {
          title: "Microsoft Learn: Drain mode in Azure Virtual Desktop",
          url: "https://learn.microsoft.com/azure/virtual-desktop/drain-mode"
        },
        {
          title: "GitHub Docs: Reviewing deployments in protected environments",
          url: "https://docs.github.com/actions/how-tos/deploy/configure-and-manage-deployments/review-deployments"
        }
      ],
      // Included intentionally so tests verify hard-steady mode strips any aside before rendering (docs/buddy-app.md section 12.7).
      aside: "That agent just needed a coffee break.",
      followUps: [
        "What does drain mode do to active users?",
        "Show the KQL query behind this check"
      ],
      plan: SAMPLE_PLAN
    },
    "deploy-failed": {
      id: "turn-deploy-01",
      threadId: "thread-deploy-failed",
      title: "Subnet overlap on first deployment",
      operatorText: "The deploy failed and I don't know why",
      mode: "soft-steady",
      events: ["run-failed"],
      answer:
        "Nothing has changed in your session host pools: the deployment stopped at the network validation step before any VM was created, so nothing needs cleaning up. " +
        "The error states that subnet `snet-avd-hosts` (`10.20.1.0/24`) overlaps an existing subnet in `vnet-contoso-spoke`. " +
        "Next step: run the preflight check with `-Fix` off in Cloud Shell and paste its last line here so we can see both ranges side by side.",
      citations: [
        { id: "call-deploy-01", label: "ARM deployment operation: NetcfgInvalidSubnet" }
      ],
      learnLinks: [
        {
          title: "Microsoft Learn: Troubleshoot subnet overlap errors",
          url: "https://learn.microsoft.com/azure/virtual-network/virtual-networks-faq"
        }
      ],
      aside: null,
      followUps: [
        "Which hosts were idle all weekend?",
        "Drain host 3 and restart its agent"
      ],
      plan: null
    },
    "security-finding": {
      id: "turn-sec-01",
      threadId: "thread-security-finding",
      title: "Defender alert on session host",
      operatorText: "Defender flagged something on the hosts, is it bad?",
      mode: "hard-steady",
      events: ["security-finding"],
      answer:
        "Microsoft Defender for Cloud reported a High severity alert on `sh-pooled-05` (`Suspicious PowerShell command line`). " +
        "Nothing has been modified by avd-buddy. Safe first step: put `sh-pooled-05` in drain mode so no new sessions land on it while you inspect the host's security event log.",
      citations: [
        { id: "call-sec-01", label: "SecurityAlert (High severity, sh-pooled-05)" }
      ],
      learnLinks: [
        {
          title: "Microsoft Learn: Security alerts in Defender for Cloud",
          url: "https://learn.microsoft.com/azure/defender-for-cloud/alerts-reference"
        }
      ],
      aside: "Never a dull moment.",
      followUps: [
        "Drain host 3 and restart its agent"
      ],
      plan: null
    },
    "max-sessions": {
      id: "turn-max-01",
      threadId: "thread-max-sessions",
      title: "Why maxSessionLimit is 8",
      operatorText: "Why is max sessions set to 8?",
      mode: "light",
      events: [],
      answer:
        "`hp-contoso-prod` has `maxSessionLimit` set to `8` on `Standard_D4s_v5` hosts (4 vCPUs, 16 GiB RAM), which matches the sizing decision recorded when the pool was built for knowledge-worker workloads (2 sessions per vCPU, 2 GiB RAM per user). " +
        "Want the longer version on how CPU oversubscription affects logon latency?",
      citations: [
        { id: "call-c11-01", label: "C11 current-state fact: hostPool.maxSessionLimit = 8 (C15: AVD-HP-004)" },
        { id: "call-brain-01", label: "Concierge lifecycle record: sizing decision for hp-contoso-prod" }
      ],
      learnLinks: [
        {
          title: "Microsoft Learn: Session host virtual machine sizing guidelines",
          url: "https://learn.microsoft.com/windows-server/remote/remote-desktop-services/virtual-machine-recs"
        }
      ],
      aside: "Eight is cozy without turning the CPU into a toaster.",
      followUps: [
        "Which hosts were idle all weekend?",
        "Drain host 3 and restart its agent"
      ],
      plan: null
    }
  };

  const HISTORY_ITEMS = [
    {
      id: "thread-idle-hosts",
      title: "Weekend idle hosts in Sales pool",
      updatedAt: "Today, 09:14",
      presetKey: "idle-hosts"
    },
    {
      id: "thread-drain-host-03",
      title: "Drain sh-pooled-03 and restart agent",
      updatedAt: "Today, 08:42",
      presetKey: "drain-host"
    },
    {
      id: "thread-deploy-failed",
      title: "Subnet overlap on first deployment",
      updatedAt: "Yesterday",
      presetKey: "deploy-failed"
    },
    {
      id: "thread-max-sessions",
      title: "Why maxSessionLimit is 8",
      updatedAt: "2 days ago",
      presetKey: "max-sessions"
    }
  ];

  function matchFixtureReply(operatorText) {
    const q = String(operatorText || "").toLowerCase();
    if (q.includes("drain") || q.includes("restart") || q.includes("approve") || q.includes("plan")) {
      return Object.assign({}, PRESET_TURNS["drain-host"], { operatorText: String(operatorText) });
    }
    if (q.includes("fail") || q.includes("error") || q.includes("urgent") || q.includes("down") || q.includes("stuck")) {
      return Object.assign({}, PRESET_TURNS["deploy-failed"], { operatorText: String(operatorText) });
    }
    if (q.includes("defender") || q.includes("security") || q.includes("breach")) {
      return Object.assign({}, PRESET_TURNS["security-finding"], { operatorText: String(operatorText) });
    }
    if (q.includes("max session") || q.includes("8") || q.includes("why is")) {
      return Object.assign({}, PRESET_TURNS["max-sessions"], { operatorText: String(operatorText) });
    }
    return Object.assign({}, PRESET_TURNS["idle-hosts"], { operatorText: String(operatorText) });
  }

  global.AVD_BUDDY_FIXTURES = {
    INITIAL_STATUS: INITIAL_STATUS,
    SWITCH_MEANINGS: SWITCH_MEANINGS,
    CLOUD_SHELL_RESET_BLOCK: CLOUD_SHELL_RESET_BLOCK,
    SAMPLE_PLAN: SAMPLE_PLAN,
    PRESET_TURNS: PRESET_TURNS,
    HISTORY_ITEMS: HISTORY_ITEMS,
    matchFixtureReply: matchFixtureReply
  };
})(typeof window !== "undefined" ? window : globalThis);
