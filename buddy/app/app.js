/* avd-buddy client application (docs/buddy-app.md).
   Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
   Front end only: holds no token, secret or estate data in browser storage; renders plans from the plan store with
   a single external GitHub link and no in-app approval button; enforces voice/calm rules before rendering any aside. */

(function () {
  "use strict";

  const SETTINGS_KEY = "avd-buddy-settings";
  const HARD_STEADY_EVENTS = new Set([
    "plan-shown",
    "approval-pending",
    "destructive-step",
    "kill-switch",
    "security-finding",
    "data-loss-risk",
    "gate-refused"
  ]);

  const DEFAULT_SETTINGS = {
    humor: "light",
    depth: "short",
    voice: "off",
    theme: "system",
    installDismissed: false
  };

  const state = {
    settings: loadSettings(),
    simMode: false,
    simOffline: false,
    status: {
      switchPosition: "run",
      switchMeaning: "Normal operation. Agents may read and propose plans; approved plans may run.",
      tokenSpendUsd: 0,
      tokenCapUsd: 15,
      waitingItems: []
    },
    history: [],
    turns: [],
    finishedAnswers: 0,
    repliesSinceAside: 99,
    deferredInstallPrompt: null,
    waitingServiceWorker: null,
    draftInMemory: "",
    listening: false,
    recognition: null,
    speakingTurnId: null
  };

  function loadSettings() {
    try {
      const raw = window.localStorage.getItem(SETTINGS_KEY);
      if (!raw) {
        return Object.assign({}, DEFAULT_SETTINGS);
      }
      const parsed = JSON.parse(raw);
      return {
        humor: parsed.humor === "off" ? "off" : "light",
        depth: parsed.depth === "longer" ? "longer" : "short",
        voice: ["off", "on", "auto"].includes(parsed.voice) ? parsed.voice : "off",
        theme: ["system", "light", "dark"].includes(parsed.theme) ? parsed.theme : "system",
        installDismissed: Boolean(parsed.installDismissed)
      };
    } catch (_err) {
      return Object.assign({}, DEFAULT_SETTINGS);
    }
  }

  function saveSettings() {
    try {
      // Purge any non-settings keys so only settings stay in localStorage (docs/buddy-app.md section 8).
      const allowed = new Set([SETTINGS_KEY, "avd-ops-theme", "avd-ops-runin"]);
      for (let i = window.localStorage.length - 1; i >= 0; i -= 1) {
        const key = window.localStorage.key(i);
        if (key && !allowed.has(key)) {
          window.localStorage.removeItem(key);
        }
      }
      window.localStorage.setItem(SETTINGS_KEY, JSON.stringify(state.settings));
    } catch (_err) {
      // Ignore storage quota / private browsing errors
    }
  }

  function applyThemeSetting(themeChoice) {
    const root = document.documentElement;
    if (themeChoice === "light" || themeChoice === "dark") {
      root.setAttribute("data-theme", themeChoice);
    } else {
      root.removeAttribute("data-theme");
    }
  }

  function isOffline() {
    return state.simOffline || (typeof navigator !== "undefined" && navigator.onLine === false);
  }

  function isStandaloneMode() {
    return (
      (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) ||
      window.navigator.standalone === true
    );
  }

  function isAppleMobile() {
    const ua = (navigator.userAgent || "").toLowerCase();
    return /iphone|ipad|ipod/.test(ua);
  }

  function getSpeechRecognitionCtor() {
    if (typeof window === "undefined") {
      return null;
    }
    return window.SpeechRecognition || window.webkitSpeechRecognition || null;
  }

  function hasSpeechSynthesis() {
    return (
      typeof window !== "undefined" &&
      "speechSynthesis" in window &&
      typeof window.SpeechSynthesisUtterance === "function"
    );
  }

  function syncSpeakButtons() {
    document.querySelectorAll(".msg-speak-btn").forEach((btn) => {
      const tid = btn.getAttribute("data-turn-id") || "";
      const active = Boolean(state.speakingTurnId && state.speakingTurnId === tid);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
      btn.setAttribute("aria-label", active ? "Stop reading answer" : "Read answer aloud");
      btn.textContent = active ? "Stop" : "Speak";
    });
  }

  function stopSpeaking() {
    if (typeof window !== "undefined" && "speechSynthesis" in window && window.speechSynthesis) {
      try {
        window.speechSynthesis.cancel();
      } catch (_err) {
        // Ignore browser synthesis errors
      }
    }
    state.speakingTurnId = null;
    syncSpeakButtons();
  }

  function buildSpokenText(turn, resolvedMode) {
    const cleanAnswer = String(turn.answer || "").replace(/`/g, "");
    if (canShowAside(turn, resolvedMode)) {
      return cleanAnswer + " Aside: " + turn.aside.trim();
    }
    return cleanAnswer;
  }

  function speakTurn(turn, resolvedMode) {
    if (!turn || state.settings.voice === "off") {
      return;
    }
    if (state.status.switchPosition === "halt-agents" || state.status.switchPosition === "halt-all") {
      stopSpeaking();
      return;
    }
    const turnId = String(turn.id || "");
    if (state.speakingTurnId && state.speakingTurnId === turnId) {
      stopSpeaking();
      return;
    }
    stopSpeaking();
    const text = buildSpokenText(turn, resolvedMode).trim();
    if (!text) {
      return;
    }
    state.speakingTurnId = turnId;
    syncSpeakButtons();
    if (!hasSpeechSynthesis()) {
      announcePolite("Voice read-aloud is not supported in this browser.");
      state.speakingTurnId = null;
      syncSpeakButtons();
      return;
    }
    try {
      const utterance = new window.SpeechSynthesisUtterance(text);
      utterance.lang = document.documentElement.lang || "en-US";
      utterance.onend = () => {
        if (state.speakingTurnId === turnId) {
          state.speakingTurnId = null;
          syncSpeakButtons();
        }
      };
      utterance.onerror = () => {
        if (state.speakingTurnId === turnId) {
          state.speakingTurnId = null;
          syncSpeakButtons();
        }
      };
      window.speechSynthesis.speak(utterance);
    } catch (_err) {
      state.speakingTurnId = null;
      syncSpeakButtons();
    }
  }

  function stopListening() {
    if (state.recognition) {
      try {
        state.recognition.stop();
      } catch (_err) {
        // Ignore
      }
      state.recognition = null;
    }
    state.listening = false;
    updateVoiceUI();
  }

  function toggleListening() {
    if (state.listening) {
      stopListening();
      return;
    }
    if (
      state.settings.voice === "off" ||
      isOffline() ||
      state.status.switchPosition === "halt-agents" ||
      state.status.switchPosition === "halt-all"
    ) {
      return;
    }
    const RecognitionCtor = getSpeechRecognitionCtor();
    if (!RecognitionCtor) {
      announcePolite("Speech recognition is not supported in this browser.");
      updateVoiceUI();
      return;
    }
    stopSpeaking();
    try {
      const recognition = new RecognitionCtor();
      recognition.lang = document.documentElement.lang || "en-US";
      recognition.interimResults = false;
      recognition.maxAlternatives = 1;
      recognition.continuous = false;
      recognition.onresult = (event) => {
        const results = event && event.results;
        if (!results || !results[0] || !results[0][0]) {
          return;
        }
        const transcript = String(results[0][0].transcript || "").trim();
        if (!transcript) {
          return;
        }
        const inputEl = document.getElementById("composer-input");
        if (inputEl && !inputEl.disabled) {
          const existing = inputEl.value.trim();
          inputEl.value = existing ? existing + " " + transcript : transcript;
          state.draftInMemory = inputEl.value;
          inputEl.focus();
          announcePolite("Dictated into composer: " + transcript);
        }
      };
      recognition.onend = () => {
        state.listening = false;
        state.recognition = null;
        updateVoiceUI();
      };
      recognition.onerror = () => {
        state.listening = false;
        state.recognition = null;
        updateVoiceUI();
      };
      state.recognition = recognition;
      state.listening = true;
      updateVoiceUI();
      recognition.start();
    } catch (_err) {
      state.listening = false;
      state.recognition = null;
      updateVoiceUI();
    }
  }

  function updateVoiceUI() {
    const micBtn = document.getElementById("composer-mic");
    if (!micBtn) {
      return;
    }
    const voiceEnabled = state.settings.voice !== "off";
    micBtn.hidden = !voiceEnabled;
    if (!voiceEnabled) {
      micBtn.setAttribute("aria-pressed", "false");
      micBtn.textContent = "Mic";
      return;
    }
    const halted =
      state.status.switchPosition === "halt-agents" || state.status.switchPosition === "halt-all";
    const supported = Boolean(getSpeechRecognitionCtor());
    micBtn.disabled = isOffline() || halted || !supported;
    micBtn.setAttribute("aria-pressed", state.listening ? "true" : "false");
    micBtn.setAttribute(
      "aria-label",
      !supported
        ? "Microphone dictation unavailable in this browser"
        : state.listening
          ? "Stop microphone dictation"
          : "Dictate question with microphone"
    );
    micBtn.textContent = state.listening ? "Stop mic" : "Mic";
  }

  function resolveTurnMode(turn) {
    if (state.status.switchPosition !== "run") {
      return "hard-steady";
    }
    if (turn.plan) {
      return "hard-steady";
    }
    const events = Array.isArray(turn.events) ? turn.events : [];
    for (const ev of events) {
      if (HARD_STEADY_EVENTS.has(ev)) {
        return "hard-steady";
      }
    }
    if (turn.mode === "hard-steady" || turn.mode === "soft-steady") {
      return turn.mode;
    }
    return "light";
  }

  function canShowAside(turn, resolvedMode) {
    if (!turn.aside || typeof turn.aside !== "string") {
      return false;
    }
    if (state.settings.humor === "off") {
      return false;
    }
    if (resolvedMode === "hard-steady") {
      return false;
    }
    const text = turn.aside.trim();
    if (!text || text.length > 140) {
      return false;
    }
    // An aside can never carry a fact: no digits, links, backticks or resource IDs (docs/buddy-voice.md section 6).
    if (/\d|https?:\/\/|www\.|`|\/subscriptions\//i.test(text)) {
      return false;
    }
    return true;
  }

  // Safe subset Markdown renderer: creates DOM nodes with textContent only, never innerHTML with untrusted text (section 8).
  function appendSafeFormattedText(container, text) {
    const parts = String(text || "").split(/(`[^`]+`)/g);
    for (const part of parts) {
      if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
        const code = document.createElement("code");
        code.textContent = part.slice(1, -1);
        container.appendChild(code);
      } else if (part) {
        container.appendChild(document.createTextNode(part));
      }
    }
  }

  function createPlanCardElement(plan) {
    const card = document.createElement("article");
    card.className = "plan-card";
    card.setAttribute("aria-label", "Proposed change plan " + String(plan.planId || ""));

    const header = document.createElement("div");
    header.className = "plan-card-header";

    const title = document.createElement("h3");
    title.className = "plan-card-title";
    title.textContent = "Proposed plan: " + String(plan.action || "");

    const hashPrefix = String(plan.planHash || "").slice(0, 12);
    const meta = document.createElement("span");
    meta.className = "plan-id-meta";
    meta.textContent = "ID: " + String(plan.planId || "") + " · Hash: " + hashPrefix;

    header.appendChild(title);
    header.appendChild(meta);
    card.appendChild(header);

    const dl = document.createElement("dl");
    dl.className = "plan-dl";

    function addRow(termText, buildValue) {
      const dt = document.createElement("dt");
      dt.textContent = termText;
      const dd = document.createElement("dd");
      buildValue(dd);
      dl.appendChild(dt);
      dl.appendChild(dd);
    }

    addRow("Action", (dd) => {
      const code = document.createElement("code");
      code.textContent = String(plan.action || "");
      dd.appendChild(code);
    });

    addRow("Targets", (dd) => {
      const targets = Array.isArray(plan.targets) ? plan.targets.join(", ") : String(plan.targets || "");
      dd.textContent = targets;
    });

    addRow("Why", (dd) => {
      dd.textContent = String(plan.why || "");
    });

    addRow("Expected effect", (dd) => {
      dd.textContent = String(plan.expectedEffect || "");
    });

    addRow("Blast radius", (dd) => {
      dd.textContent = String(plan.blastRadius || "");
    });

    addRow("Gate results", (dd) => {
      const ul = document.createElement("ul");
      ul.className = "gate-list";
      const gates = Array.isArray(plan.gateResults) ? plan.gateResults : [];
      for (const g of gates) {
        const li = document.createElement("li");
        const pass = g.outcome === "pass";
        li.className = "gate-item " + (pass ? "gate-pass" : "gate-fail");
        li.textContent = String(g.gate || "") + " — " + String(g.text || (pass ? "Pass" : "Fail"));
        ul.appendChild(li);
      }
      dd.appendChild(ul);
    });

    addRow("Rollback", (dd) => {
      const code = document.createElement("code");
      code.textContent = String(plan.rollback || "");
      dd.appendChild(code);
    });

    addRow("Expiry", (dd) => {
      dd.textContent = String(plan.expiry || "");
    });

    addRow("Plan ID & hash", (dd) => {
      const code = document.createElement("code");
      code.textContent = String(plan.planId || "") + " (" + hashPrefix + ")";
      dd.appendChild(code);
    });

    card.appendChild(dl);

    const verifyNote = document.createElement("p");
    verifyNote.className = "plan-verify-note";
    verifyNote.textContent =
      "Before approving in GitHub, check that the approval run displays plan ID " +
      String(plan.planId || "") +
      " and hash prefix " +
      hashPrefix +
      ". avd-buddy never approves changes inside the app.";
    card.appendChild(verifyNote);

    const approveUrl = String(plan.approveUrl || "https://github.com/avd-ops/avd-swarm/actions");
    let hostLabel = "github.com";
    try {
      hostLabel = new URL(approveUrl).host;
    } catch (_err) {
      hostLabel = "github.com";
    }

    const link = document.createElement("a");
    link.className = "plan-github-link";
    link.href = approveUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "Review and approve in GitHub (" + hostLabel + ") ↗";
    card.appendChild(link);

    return card;
  }

  function renderTurnElement(turn) {
    const wrapper = document.createElement("div");
    wrapper.className = "msg-turn";

    if (turn.operatorText) {
      const opBubble = document.createElement("div");
      opBubble.className = "msg-operator";
      opBubble.textContent = String(turn.operatorText);
      wrapper.appendChild(opBubble);
    }

    const buddyRow = document.createElement("div");
    buddyRow.className = "msg-buddy";

    const avatar = document.createElement("img");
    avatar.src = "icons/avatar.svg";
    avatar.alt = "";
    avatar.className = "buddy-avatar";
    avatar.width = 32;
    avatar.height = 32;
    buddyRow.appendChild(avatar);

    const body = document.createElement("div");
    body.className = "msg-buddy-body";

    const resolvedMode = resolveTurnMode(turn);

    const metaRow = document.createElement("div");
    metaRow.className = "msg-meta-row";

    const sender = document.createElement("span");
    sender.className = "msg-sender";
    sender.textContent = "avd-buddy";

    const modeBadge = document.createElement("span");
    modeBadge.className = "msg-mode-badge";
    modeBadge.textContent = "mode: " + resolvedMode;

    metaRow.appendChild(sender);
    metaRow.appendChild(modeBadge);

    if (!turn.thinking && state.settings.voice !== "off") {
      const speakBtn = document.createElement("button");
      speakBtn.type = "button";
      speakBtn.className = "msg-speak-btn";
      const turnId = String(turn.id || "");
      const isSpeaking = Boolean(state.speakingTurnId && state.speakingTurnId === turnId);
      speakBtn.setAttribute("data-turn-id", turnId);
      speakBtn.setAttribute("aria-pressed", isSpeaking ? "true" : "false");
      speakBtn.setAttribute("aria-label", isSpeaking ? "Stop reading answer" : "Read answer aloud");
      speakBtn.textContent = isSpeaking ? "Stop" : "Speak";
      speakBtn.addEventListener("click", () => {
        speakTurn(turn, resolvedMode);
      });
      metaRow.appendChild(speakBtn);
    }

    body.appendChild(metaRow);

    const answerP = document.createElement("p");
    answerP.className = "msg-answer";
    if (turn.thinking) {
      answerP.textContent = "Thinking...";
      body.appendChild(answerP);
      buddyRow.appendChild(body);
      wrapper.appendChild(buddyRow);
      return wrapper;
    }

    appendSafeFormattedText(answerP, turn.answer || "");
    body.appendChild(answerP);

    if (Array.isArray(turn.citations) && turn.citations.length > 0) {
      const citBox = document.createElement("div");
      citBox.className = "msg-citations";
      const eyebrow = document.createElement("span");
      eyebrow.className = "eyebrow";
      eyebrow.textContent = "Sources cited";
      citBox.appendChild(eyebrow);
      const ul = document.createElement("ul");
      for (const c of turn.citations) {
        const li = document.createElement("li");
        li.textContent = String(c.label || c.id || "");
        ul.appendChild(li);
      }
      citBox.appendChild(ul);
      body.appendChild(citBox);
    }

    if (Array.isArray(turn.learnLinks) && turn.learnLinks.length > 0) {
      const learnBox = document.createElement("div");
      learnBox.className = "msg-learn";
      const eyebrow = document.createElement("span");
      eyebrow.className = "eyebrow";
      eyebrow.textContent = "Learn more";
      learnBox.appendChild(eyebrow);
      const ul = document.createElement("ul");
      for (const item of turn.learnLinks) {
        const li = document.createElement("li");
        const a = document.createElement("a");
        a.href = String(item.url || "https://learn.microsoft.com/");
        // Teaching links open in system browser so standalone PWA isn't trapped (docs/buddy-app.md section 3).
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = String(item.title || item.url);
        let host = "learn.microsoft.com";
        try {
          host = new URL(a.href).host;
        } catch (_err) {
          host = "learn.microsoft.com";
        }
        const hostSpan = document.createElement("span");
        hostSpan.className = "learn-link-host";
        hostSpan.textContent = " (" + host + " ↗)";
        li.appendChild(a);
        li.appendChild(hostSpan);
        ul.appendChild(li);
      }
      learnBox.appendChild(ul);
      body.appendChild(learnBox);
    }

    if (turn.plan) {
      body.appendChild(createPlanCardElement(turn.plan));
    }

    if (canShowAside(turn, resolvedMode)) {
      const asideBox = document.createElement("aside");
      asideBox.className = "msg-aside";
      asideBox.setAttribute("aria-label", "aside");
      const label = document.createElement("span");
      label.className = "eyebrow";
      label.textContent = "aside";
      const textSpan = document.createElement("span");
      textSpan.textContent = turn.aside.trim();
      asideBox.appendChild(label);
      asideBox.appendChild(textSpan);
      body.appendChild(asideBox);
    }

    if (resolvedMode !== "hard-steady" && Array.isArray(turn.followUps) && turn.followUps.length > 0) {
      const fuWrap = document.createElement("div");
      fuWrap.className = "follow-ups";
      for (const promptText of turn.followUps) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "follow-up-btn";
        btn.textContent = String(promptText);
        btn.addEventListener("click", () => submitQuestion(String(promptText)));
        fuWrap.appendChild(btn);
      }
      body.appendChild(fuWrap);
    }

    buddyRow.appendChild(body);
    wrapper.appendChild(buddyRow);
    return wrapper;
  }

  function renderMessages() {
    const listEl = document.getElementById("messages-list");
    const emptyEl = document.getElementById("empty-state");
    if (!listEl || !emptyEl) {
      return;
    }
    listEl.textContent = "";
    emptyEl.hidden = state.turns.length > 0;
    for (const turn of state.turns) {
      listEl.appendChild(renderTurnElement(turn));
    }
    maybeUpdateInstallHint();
  }

  function maybeUpdateInstallHint() {
    const hintEl = document.getElementById("install-hint");
    const bodyEl = document.getElementById("install-hint-body");
    const installBtn = document.getElementById("install-pwa-btn");
    if (!hintEl || !bodyEl || !installBtn) {
      return;
    }
    // Never show before the first finished answer, after dismissal, or inside the installed app (section 3 & 5).
    if (state.finishedAnswers < 1 || state.settings.installDismissed || isStandaloneMode()) {
      hintEl.hidden = true;
      return;
    }
    hintEl.hidden = false;
    if (isAppleMobile()) {
      bodyEl.textContent =
        "On iPhone or iPad in Safari: tap Share, then tap Add to Home Screen. Only the app shell is cached; on a shared device, sign out when done.";
      installBtn.hidden = true;
    } else if (state.deferredInstallPrompt) {
      bodyEl.textContent =
        "Install avd-buddy on your home screen for full-screen access. Only the app shell is cached; on a shared device, sign out when done.";
      installBtn.hidden = false;
    } else {
      bodyEl.textContent =
        "Install avd-buddy from your browser menu (Install app / Add to Home screen). Only the app shell is cached; on a shared device, sign out when done.";
      installBtn.hidden = true;
    }
  }

  function renderSwitchAndStatus() {
    const pos = state.status.switchPosition || "run";
    const pill = document.getElementById("switch-pill");
    const pillText = document.getElementById("switch-pill-text");
    const banner = document.getElementById("switch-banner");
    const bannerText = document.getElementById("switch-banner-text");
    const composerForm = document.getElementById("composer-form");
    const haltAgentsCard = document.getElementById("halt-agents-card");
    const statusBadge = document.getElementById("status-switch-badge");
    const statusName = document.getElementById("status-switch-name");
    const statusMeaning = document.getElementById("status-switch-meaning");
    const spendLine = document.getElementById("status-spend-line");
    const spendFill = document.getElementById("status-spend-fill");
    const waitingList = document.getElementById("status-waiting-list");

    const posClass = "pos-" + pos;
    if (pill && pillText) {
      pill.className = "switch-pill " + posClass;
      pillText.textContent = pos;
      pill.setAttribute("aria-label", "Kill switch position: " + pos + ". Open status");
    }

    if (statusBadge && statusName) {
      statusBadge.className = "switch-pill " + posClass;
      statusName.textContent = pos;
    }

    const meanings = (window.AVD_BUDDY_FIXTURES && window.AVD_BUDDY_FIXTURES.SWITCH_MEANINGS) || {};
    const meaningText = state.status.switchMeaning || meanings[pos] || "";
    if (statusMeaning) {
      statusMeaning.textContent = meaningText;
    }

    if (banner && bannerText) {
      if (pos === "run") {
        banner.hidden = true;
      } else {
        banner.hidden = false;
        banner.className = "switch-banner " + (pos === "halt-actions" ? "banner-caution" : "banner-bad");
        bannerText.textContent = "Kill switch is at " + pos + " — " + meaningText;
      }
    }

    if (composerForm) {
      composerForm.hidden = pos === "halt-agents" || pos === "halt-all";
    }
    if (haltAgentsCard) {
      haltAgentsCard.hidden = pos !== "halt-agents";
    }
    if (pos === "halt-agents" || pos === "halt-all") {
      stopListening();
      stopSpeaking();
    }
    updateVoiceUI();

    const spent = Number(state.status.tokenSpendUsd || 0);
    const cap = Number(state.status.tokenCapUsd || 15);
    if (spendLine) {
      spendLine.textContent = "$" + spent.toFixed(2) + " of $" + cap.toFixed(2) + " daily cap";
    }
    if (spendFill) {
      const pct = cap > 0 ? Math.min(100, Math.round((spent / cap) * 100)) : 0;
      spendFill.style.width = pct + "%";
    }

    if (waitingList) {
      waitingList.textContent = "";
      const items = Array.isArray(state.status.waitingItems) ? state.status.waitingItems : [];
      if (items.length === 0) {
        const li = document.createElement("li");
        li.textContent = "Nothing waiting on the operator right now.";
        waitingList.appendChild(li);
      } else {
        for (const item of items) {
          const li = document.createElement("li");
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "waiting-link-btn";
          btn.textContent = String(item.title || "");
          btn.addEventListener("click", () => {
            openThreadById(item.threadId);
          });
          li.appendChild(btn);
          waitingList.appendChild(li);
        }
      }
    }

    const estateCard = document.getElementById("status-estate-card");
    const estateSummary = document.getElementById("status-estate-summary");
    const estateBadge = document.getElementById("status-estate-badge");
    const estatePlanes = document.getElementById("status-estate-planes");
    const estateMeter = document.getElementById("status-estate-meter");
    const estateFindings = document.getElementById("status-estate-findings");
    const estate = state.status.estate;
    if (estateCard && estate && Array.isArray(estate.planes)) {
      estateCard.hidden = false;
      if (estateSummary) {
        estateSummary.textContent =
          "Estate " + String(estate.estate || "estate") + " · snapshot " + String(estate.collectedAt || "");
      }
      if (estateBadge) {
        const st = String(estate.overallStatus || "pass");
        estateBadge.className =
          "estate-score-pill" + (st === "fail" ? " is-fail" : st === "warn" ? " is-warn" : "");
        estateBadge.textContent = String(estate.overallScorePct || 0) + "% " + st.toUpperCase();
      }
      if (estatePlanes) {
        estatePlanes.textContent = "";
        for (const p of estate.planes) {
          const li = document.createElement("li");
          li.className = "estate-plane-row";
          const nameSpan = document.createElement("span");
          nameSpan.className = "estate-plane-name";
          nameSpan.textContent = String(p.name || p.id);
          const countSpan = document.createElement("span");
          countSpan.className = "estate-plane-counts";
          countSpan.textContent =
            String(p.scorePct || 0) + "% · " + String(p.pass || 0) + " pass · " + String(p.warn || 0) + " warn · " + String(p.fail || 0) + " fail";
          li.appendChild(nameSpan);
          li.appendChild(countSpan);
          estatePlanes.appendChild(li);
        }
      }
      if (estateMeter && estate.meterAudit) {
        const m = estate.meterAudit;
        estateMeter.textContent =
          "Meter audit: " +
          String(m.runningSessionHosts || 0) +
          " running hosts · " +
          String(m.unattachedDisks || 0) +
          " unattached disks · " +
          String(m.orphanedNics || 0) +
          " orphaned NICs · " +
          String(m.publicIps || 0) +
          " public IPs";
      }
      if (estateFindings) {
        estateFindings.textContent = "";
        const findings = Array.isArray(estate.topFindings) ? estate.topFindings : [];
        for (const f of findings) {
          const row = document.createElement("div");
          row.className = "estate-finding-row";
          const txt = document.createElement("span");
          txt.textContent = "[" + String(f.checkId || "") + "] " + String(f.title || "");
          const askBtn = document.createElement("button");
          askBtn.type = "button";
          askBtn.className = "buddy-small-btn";
          askBtn.textContent = "Ask avd-buddy";
          askBtn.addEventListener("click", () => {
            showScreen("chat");
            submitQuestion(String(f.askPrompt || "Explain finding " + String(f.checkId || "")));
          });
          row.appendChild(txt);
          row.appendChild(askBtn);
          estateFindings.appendChild(row);
        }
      }
    } else if (estateCard) {
      estateCard.hidden = true;
    }

    const resetBlock = (window.AVD_BUDDY_FIXTURES && window.AVD_BUDDY_FIXTURES.CLOUD_SHELL_RESET_BLOCK) || "";
    for (const id of ["halt-agents-code", "status-reset-code", "halted-screen-code"]) {
      const el = document.getElementById(id);
      if (el) {
        el.textContent = resetBlock;
      }
    }

    if (pos === "halt-all") {
      showScreen("halted");
    }
  }

  function renderHistory() {
    const listEl = document.getElementById("history-list");
    if (!listEl) {
      return;
    }
    listEl.textContent = "";
    for (const item of state.history) {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "history-item-btn";
      const titleSpan = document.createElement("span");
      titleSpan.textContent = String(item.title || "");
      const metaSpan = document.createElement("span");
      metaSpan.className = "history-item-meta";
      metaSpan.textContent = String(item.updatedAt || "");
      btn.appendChild(titleSpan);
      btn.appendChild(metaSpan);
      btn.addEventListener("click", () => {
        openHistoryItem(item);
      });
      li.appendChild(btn);
      listEl.appendChild(li);
    }
  }

  function openHistoryItem(item) {
    const fixtures = window.AVD_BUDDY_FIXTURES;
    if (fixtures && item.presetKey && fixtures.PRESET_TURNS[item.presetKey]) {
      state.turns = [Object.assign({}, fixtures.PRESET_TURNS[item.presetKey])];
      state.finishedAnswers = Math.max(1, state.finishedAnswers);
      renderMessages();
    }
    const panel = document.getElementById("history-panel");
    if (panel) {
      panel.classList.remove("open");
    }
    showScreen("chat");
  }

  function openThreadById(threadId) {
    const found = state.history.find((h) => h.id === threadId);
    if (found) {
      openHistoryItem(found);
      return;
    }
    showScreen("chat");
  }

  function showScreen(screenName) {
    const map = {
      chat: "screen-chat",
      status: "screen-status",
      settings: "screen-settings",
      halted: "screen-halted",
      signin: "screen-signin"
    };
    for (const [key, id] of Object.entries(map)) {
      const el = document.getElementById(id);
      if (el) {
        el.hidden = key !== screenName;
      }
    }
    const dropdown = document.getElementById("top-menu");
    const menuBtn = document.getElementById("menu-btn");
    if (dropdown && menuBtn) {
      dropdown.hidden = true;
      menuBtn.setAttribute("aria-expanded", "false");
    }
  }

  function updateOfflineUI() {
    const offline = isOffline();
    const banner = document.getElementById("offline-banner");
    const sendBtn = document.getElementById("composer-send");
    const inputEl = document.getElementById("composer-input");
    const simToggle = document.getElementById("sim-offline-toggle");
    if (banner) {
      banner.hidden = !offline;
    }
    if (sendBtn) {
      sendBtn.disabled = offline;
    }
    if (inputEl) {
      inputEl.disabled = offline;
    }
    if (simToggle) {
      simToggle.setAttribute("aria-pressed", offline ? "true" : "false");
    }
    if (offline) {
      stopListening();
    }
    updateVoiceUI();
  }

  function announcePolite(message) {
    const announcer = document.getElementById("live-announcer");
    if (announcer) {
      announcer.textContent = message;
    }
  }

  async function submitQuestion(text) {
    // Rule 6: Never send later when offline.
    if (isOffline()) {
      updateOfflineUI();
      return;
    }
    if (state.status.switchPosition === "halt-agents" || state.status.switchPosition === "halt-all") {
      return;
    }
    const trimmed = String(text || "").trim();
    if (!trimmed) {
      return;
    }

    stopListening();
    stopSpeaking();

    const inputEl = document.getElementById("composer-input");
    if (inputEl) {
      inputEl.value = "";
    }
    state.draftInMemory = trimmed;

    // Under 200ms local echo and thinking line (docs/buddy-app.md section 9).
    const pendingTurn = {
      id: "pending-" + Date.now(),
      operatorText: trimmed,
      thinking: true,
      mode: "light",
      events: []
    };
    state.turns.push(pendingTurn);
    renderMessages();

    if (state.simMode) {
      const fixtures = window.AVD_BUDDY_FIXTURES;
      const matched = fixtures.matchFixtureReply(trimmed);
      state.turns[state.turns.length - 1] = matched;
      state.finishedAnswers += 1;
      state.draftInMemory = "";
      renderMessages();
      announcePolite("avd-buddy answered: " + matched.answer);
      if (state.settings.voice === "auto") {
        speakTurn(matched, resolveTurnMode(matched));
      }
      return;
    }

    try {
      const startRes = await fetch("/api/turns", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: trimmed,
          humor: state.settings.humor,
          depth: state.settings.depth
        })
      });
      if (startRes.status === 401) {
        showSignInWithDraft();
        return;
      }
      if (!startRes.ok) {
        throw new Error("Turn request failed");
      }
      const startData = await startRes.json();
      const turnId = startData.turnId;
      let turnResult = startData.turn || null;
      let after = 0;

      while (!turnResult || turnResult.status === "running") {
        const pollRes = await fetch("/api/turns/" + encodeURIComponent(turnId) + "?after=" + after);
        if (pollRes.status === 401) {
          showSignInWithDraft();
          return;
        }
        if (!pollRes.ok) {
          throw new Error("Turn poll failed");
        }
        const pollData = await pollRes.json();
        after = pollData.nextAfter || after;
        if (pollData.done && pollData.turn) {
          turnResult = pollData.turn;
          break;
        }
      }

      state.turns[state.turns.length - 1] = turnResult;
      state.finishedAnswers += 1;
      state.draftInMemory = "";
      renderMessages();
      announcePolite("avd-buddy answered: " + (turnResult.answer || ""));
      if (state.settings.voice === "auto") {
        speakTurn(turnResult, resolveTurnMode(turnResult));
      }
    } catch (_err) {
      state.turns.pop();
      renderMessages();
      showScreen("halted");
    }
  }

  function showSignInWithDraft() {
    const note = document.getElementById("signin-draft-note");
    if (note) {
      if (state.draftInMemory) {
        note.hidden = false;
        note.textContent = "Sign in again to continue. Your draft message is held in memory only for this tab.";
      } else {
        note.hidden = true;
      }
    }
    showScreen("signin");
  }

  async function tripKillSwitch(targetPosition, confirmationText) {
    const feedback = document.getElementById("trip-feedback");
    if (confirmationText.trim() !== targetPosition) {
      if (feedback) {
        feedback.textContent = "Type \"" + targetPosition + "\" exactly to confirm tripping the switch.";
      }
      return;
    }

    if (state.simMode) {
      setSimSwitchPosition(targetPosition);
      if (feedback) {
        feedback.textContent = "Kill switch tripped to " + targetPosition + ".";
      }
      return;
    }

    try {
      const res = await fetch("/api/switch/trip", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          position: targetPosition,
          confirm: confirmationText.trim()
        })
      });
      if (!res.ok) {
        throw new Error("Trip failed");
      }
      const data = await res.json();
      state.status.switchPosition = data.switchPosition || targetPosition;
      state.status.switchMeaning = data.switchMeaning || "";
      renderSwitchAndStatus();
      renderMessages();
    } catch (_err) {
      showScreen("halted");
    }
  }

  function setSimSwitchPosition(pos) {
    const fixtures = window.AVD_BUDDY_FIXTURES;
    state.status.switchPosition = pos;
    state.status.switchMeaning = (fixtures && fixtures.SWITCH_MEANINGS[pos]) || "";
    const confirmBox = document.getElementById("trip-confirm-box");
    if (confirmBox) {
      confirmBox.hidden = true;
    }
    renderSwitchAndStatus();
    renderMessages();
    if (pos !== "halt-all") {
      showScreen("chat");
    }
  }

  async function signOut() {
    stopListening();
    stopSpeaking();
    state.turns = [];
    state.history = [];
    state.draftInMemory = "";
    try {
      window.sessionStorage.clear();
    } catch (_err) {
      // Ignore
    }
    saveSettings();
    if (window.caches && typeof window.caches.keys === "function") {
      try {
        const keys = await window.caches.keys();
        await Promise.all(keys.map((k) => window.caches.delete(k)));
      } catch (_err) {
        // Ignore
      }
    }
    if (!state.simMode) {
      window.location.assign("/.auth/logout");
      return;
    }
    renderMessages();
    showScreen("signin");
  }

  function enableSimMode() {
    const fixtures = window.AVD_BUDDY_FIXTURES;
    state.simMode = true;
    if (fixtures) {
      state.status = Object.assign({}, fixtures.INITIAL_STATUS);
      state.history = fixtures.HISTORY_ITEMS.slice();
    }
    const simBar = document.getElementById("sim-bar");
    if (simBar) {
      simBar.hidden = false;
    }
    renderSwitchAndStatus();
    renderHistory();
    renderMessages();
    showScreen("chat");
  }

  async function bootstrap() {
    applyThemeSetting(state.settings.theme);

    const humorSelect = document.getElementById("setting-humor");
    const depthSelect = document.getElementById("setting-depth");
    const voiceSelect = document.getElementById("setting-voice");
    const themeSelect = document.getElementById("setting-theme");
    if (humorSelect) {
      humorSelect.value = state.settings.humor;
    }
    if (depthSelect) {
      depthSelect.value = state.settings.depth;
    }
    if (voiceSelect) {
      voiceSelect.value = state.settings.voice;
    }
    if (themeSelect) {
      themeSelect.value = state.settings.theme;
    }
    updateVoiceUI();

    const params = new URLSearchParams(window.location.search);
    const host = (window.location.hostname || "").toLowerCase();
    if (
      params.get("sim") === "1" ||
      params.get("demo") === "1" ||
      host.endsWith(".github.io") ||
      host === "buddy.avd-ops.dev"
    ) {
      enableSimMode();
      return;
    }

    try {
      const res = await fetch("/api/status", { headers: { Accept: "application/json" } });
      if (res.status === 401) {
        showScreen("signin");
        return;
      }
      if (res.status === 404) {
        // Static hosting without /api (such as GitHub Pages demo): run in public demo simulation mode.
        enableSimMode();
        return;
      }
      if (!res.ok) {
        showScreen("halted");
        return;
      }
      const statusData = await res.json();
      state.simMode = Boolean(statusData.simMode);
      state.status = statusData;
      const simBar = document.getElementById("sim-bar");
      if (simBar) {
        simBar.hidden = !state.simMode;
      }

      const histRes = await fetch("/api/history", { headers: { Accept: "application/json" } });
      if (histRes.ok) {
        const histData = await histRes.json();
        state.history = Array.isArray(histData.items) ? histData.items : [];
      }
      renderSwitchAndStatus();
      renderHistory();
      renderMessages();
    } catch (_err) {
      if (window.location.protocol === "file:") {
        enableSimMode();
      } else {
        renderSwitchAndStatus();
        showScreen("halted");
      }
    }
  }

  function registerServiceWorker() {
    if (!("serviceWorker" in navigator) || window.location.protocol === "file:") {
      return;
    }
    navigator.serviceWorker
      .register("sw.js")
      .then((reg) => {
        if (reg.waiting) {
          state.waitingServiceWorker = reg.waiting;
          const banner = document.getElementById("sw-update-banner");
          if (banner) {
            banner.hidden = false;
          }
        }
        reg.addEventListener("updatefound", () => {
          const installing = reg.installing;
          if (!installing) {
            return;
          }
          installing.addEventListener("statechange", () => {
            if (installing.state === "installed" && navigator.serviceWorker.controller) {
              state.waitingServiceWorker = installing;
              const banner = document.getElementById("sw-update-banner");
              if (banner) {
                banner.hidden = false;
              }
            }
          });
        });
      })
      .catch(() => {
        // Service worker registration is optional in non-HTTPS test runners
      });
  }

  function wireEvents() {
    const brandBtn = document.getElementById("brand-settings-btn");
    if (brandBtn) {
      brandBtn.addEventListener("click", () => showScreen("settings"));
    }

    const switchPill = document.getElementById("switch-pill");
    if (switchPill) {
      switchPill.addEventListener("click", () => showScreen("status"));
    }

    const bannerStatusBtn = document.getElementById("switch-banner-status-btn");
    if (bannerStatusBtn) {
      bannerStatusBtn.addEventListener("click", () => showScreen("status"));
    }

    const menuBtn = document.getElementById("menu-btn");
    const dropdown = document.getElementById("top-menu");
    if (menuBtn && dropdown) {
      menuBtn.addEventListener("click", () => {
        const nextHidden = !dropdown.hidden;
        dropdown.hidden = nextHidden;
        menuBtn.setAttribute("aria-expanded", nextHidden ? "false" : "true");
      });
    }

    document.querySelectorAll("[data-nav]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = btn.getAttribute("data-nav");
        if (target === "history") {
          const panel = document.getElementById("history-panel");
          if (panel) {
            panel.classList.toggle("open");
          }
          if (dropdown && menuBtn) {
            dropdown.hidden = true;
            menuBtn.setAttribute("aria-expanded", "false");
          }
          return;
        }
        if (target) {
          showScreen(target);
        }
      });
    });

    const closeHistBtn = document.getElementById("close-history-btn");
    if (closeHistBtn) {
      closeHistBtn.addEventListener("click", () => {
        const panel = document.getElementById("history-panel");
        if (panel) {
          panel.classList.remove("open");
        }
      });
    }

    const newChatBtn = document.getElementById("new-chat-btn");
    if (newChatBtn) {
      newChatBtn.addEventListener("click", () => {
        stopListening();
        stopSpeaking();
        state.turns = [];
        renderMessages();
        showScreen("chat");
      });
    }

    document.querySelectorAll("[data-prompt]").forEach((btn) => {
      btn.addEventListener("click", () => {
        submitQuestion(btn.getAttribute("data-prompt") || "");
      });
    });

    const composerForm = document.getElementById("composer-form");
    const composerInput = document.getElementById("composer-input");
    const composerMic = document.getElementById("composer-mic");
    if (composerForm && composerInput) {
      composerForm.addEventListener("submit", (ev) => {
        ev.preventDefault();
        submitQuestion(composerInput.value);
      });
      // Hardware keyboard on iPad/desktop: Enter sends, Shift+Enter adds a line (docs/buddy-app.md section 5).
      composerInput.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" && !ev.shiftKey) {
          ev.preventDefault();
          submitQuestion(composerInput.value);
        }
      });
    }
    if (composerMic) {
      composerMic.addEventListener("click", () => {
        toggleListening();
      });
    }

    // Install hint events
    window.addEventListener("beforeinstallprompt", (ev) => {
      ev.preventDefault();
      state.deferredInstallPrompt = ev;
      maybeUpdateInstallHint();
    });

    const dismissInstallBtn = document.getElementById("dismiss-install-btn");
    if (dismissInstallBtn) {
      dismissInstallBtn.addEventListener("click", () => {
        state.settings.installDismissed = true;
        saveSettings();
        maybeUpdateInstallHint();
      });
    }

    const installPwaBtn = document.getElementById("install-pwa-btn");
    if (installPwaBtn) {
      installPwaBtn.addEventListener("click", async () => {
        if (!state.deferredInstallPrompt) {
          return;
        }
        state.deferredInstallPrompt.prompt();
        state.deferredInstallPrompt = null;
        state.settings.installDismissed = true;
        saveSettings();
        maybeUpdateInstallHint();
      });
    }

    // Kill switch trip confirmation events
    const openTripBtn = document.getElementById("open-trip-confirm-btn");
    const tripBox = document.getElementById("trip-confirm-box");
    const cancelTripBtn = document.getElementById("cancel-trip-btn");
    const tripSelect = document.getElementById("trip-target-select");
    const tripExpectedCode = document.getElementById("trip-expected-code");
    const tripInput = document.getElementById("trip-confirm-input");
    const confirmTripBtn = document.getElementById("confirm-trip-btn");

    if (openTripBtn && tripBox) {
      openTripBtn.addEventListener("click", () => {
        tripBox.hidden = false;
      });
    }
    if (cancelTripBtn && tripBox) {
      cancelTripBtn.addEventListener("click", () => {
        tripBox.hidden = true;
      });
    }
    if (tripSelect && tripExpectedCode && tripInput) {
      tripSelect.addEventListener("change", () => {
        tripExpectedCode.textContent = tripSelect.value;
        tripInput.placeholder = tripSelect.value;
      });
    }
    if (confirmTripBtn && tripSelect && tripInput) {
      confirmTripBtn.addEventListener("click", () => {
        tripKillSwitch(tripSelect.value, tripInput.value);
      });
    }

    // Settings changes
    const humorSelect = document.getElementById("setting-humor");
    if (humorSelect) {
      humorSelect.addEventListener("change", () => {
        state.settings.humor = humorSelect.value === "off" ? "off" : "light";
        saveSettings();
        renderMessages();
      });
    }
    const depthSelect = document.getElementById("setting-depth");
    if (depthSelect) {
      depthSelect.addEventListener("change", () => {
        state.settings.depth = depthSelect.value === "longer" ? "longer" : "short";
        saveSettings();
      });
    }
    const voiceSelect = document.getElementById("setting-voice");
    if (voiceSelect) {
      voiceSelect.addEventListener("change", () => {
        const val = voiceSelect.value;
        state.settings.voice = ["off", "on", "auto"].includes(val) ? val : "off";
        if (state.settings.voice === "off") {
          stopListening();
          stopSpeaking();
        }
        saveSettings();
        updateVoiceUI();
        renderMessages();
      });
    }
    const themeSelect = document.getElementById("setting-theme");
    if (themeSelect) {
      themeSelect.addEventListener("change", () => {
        state.settings.theme = themeSelect.value;
        saveSettings();
        applyThemeSetting(state.settings.theme);
      });
    }

    // Sign out buttons
    for (const id of ["menu-signout-btn", "settings-signout-btn"]) {
      const btn = document.getElementById(id);
      if (btn) {
        btn.addEventListener("click", () => signOut());
      }
    }

    // Retry service & enter sim buttons
    const retryBtn = document.getElementById("retry-service-btn");
    if (retryBtn) {
      retryBtn.addEventListener("click", () => {
        if (state.simMode) {
          setSimSwitchPosition("run");
        } else {
          bootstrap();
        }
      });
    }
    const enterSimBtn = document.getElementById("enter-sim-btn");
    if (enterSimBtn) {
      enterSimBtn.addEventListener("click", () => enableSimMode());
    }

    // Service worker update reload button
    const swReloadBtn = document.getElementById("sw-reload-btn");
    if (swReloadBtn) {
      swReloadBtn.addEventListener("click", () => {
        if (state.waitingServiceWorker) {
          state.waitingServiceWorker.postMessage({ type: "SKIP_WAITING" });
        }
        window.location.reload();
      });
    }

    // Simulation bar preset and switch buttons
    document.querySelectorAll("[data-preset]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.getAttribute("data-preset");
        const fixtures = window.AVD_BUDDY_FIXTURES;
        if (fixtures && key && fixtures.PRESET_TURNS[key]) {
          if (state.status.switchPosition === "halt-all") {
            setSimSwitchPosition("run");
          }
          const presetTurn = Object.assign({}, fixtures.PRESET_TURNS[key]);
          state.turns.push(presetTurn);
          state.finishedAnswers += 1;
          renderMessages();
          showScreen("chat");
          if (state.settings.voice === "auto") {
            speakTurn(presetTurn, resolveTurnMode(presetTurn));
          }
        }
      });
    });

    document.querySelectorAll("[data-sim-switch]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const pos = btn.getAttribute("data-sim-switch") || "run";
        setSimSwitchPosition(pos);
      });
    });

    const simOfflineBtn = document.getElementById("sim-offline-toggle");
    if (simOfflineBtn) {
      simOfflineBtn.addEventListener("click", () => {
        state.simOffline = !state.simOffline;
        updateOfflineUI();
      });
    }

    window.addEventListener("online", updateOfflineUI);
    window.addEventListener("offline", updateOfflineUI);
  }

  document.addEventListener("DOMContentLoaded", () => {
    wireEvents();
    updateOfflineUI();
    registerServiceWorker();
    bootstrap();
  });
})();
