# avd-buddy as an installable web app

- **Status:** Proposed (2026-10-20). Spec only: nothing is built, nothing runs, no spend.
- **Asked for by the owner (2026-10-20):** a mobile app for the chatbot, which can just be an installable web app for now, with the icon in [`assets/buddy/`](../assets/buddy/README.md). It is the chatbot's app, not a general AVD console: every screen serves the conversation.
- **Builds in:** build order step 2 (spec §9a), with avd-buddy itself; push notifications not before step 3. Code lives in `buddy/app/` (spec SQ7).
- **Changes nothing else:** avd-buddy is still the front end of spec §1 and §3. This document says how that front end installs on a phone and behaves there. It adds no contract, agent, identity with Azure roles, or write path.

## 1. What it is, in one paragraph

avd-buddy is a progressive web app (PWA): the same static chat page from spec §3, plus a web app manifest and a service worker, so an operator can add it to the home screen of an iPhone, iPad or Android phone and open it like an app, full screen, with its own icon. It signs in with Entra through Static Web Apps, holds no token and no secret, caches only its own code, and shows plans and approval links exactly as spec §5 says. Approving still happens in GitHub, on the exact plan, never in the app.

## 2. Rules

These hold in every phase; each is a test in §12.

1. **Front end only.** The app relays the conversation and renders what the API returns. It has no logic that decides anything (spec §1), no write credential, and no "approve" or "confirm" button. Every change is a plan from the plan store that a person approves in the GitHub Environment `approve` (spec §5, steps 4 to 6).
2. **No token or secret in the client.** Sign-in is Static Web Apps' built-in auth (§4): the browser holds only an HTTP-only session cookie. No MSAL cache, no access token, no API key in JavaScript, storage or the bundle.
3. **No IDs in the bundle.** No tenant, client, subscription or object ID ships in the static files. They are app settings the adopter's deployment fills in (spec §6, identities.md rule).
4. **No requests to other sites.** The page fetches only from its own origin: no CDN, font service, analytics, error reporter or third-party script. A content security policy enforces it (§8). Entra sign-in and "learn more" links are navigations the operator starts, not fetches. Push (§7), if it ships, is the one exception, and it carries nothing.
5. **Nothing from the estate is kept on the device.** The service worker caches the app shell only. Conversations, plans, facts and names stay on the server; signing out leaves nothing behind (§8).
6. **Never send later.** A message typed while offline is not queued. A "fix it" delivered an hour late is a different request.
7. **Tone never changes a fact** ([buddy-voice.md](buddy-voice.md) §1). Plan cards, the switch position, spend figures and gate results render in fixed layouts from data, in every mode.

## 3. Screens

One main screen, the conversation. Everything else opens from it and returns to it.

| Screen | What it shows | Notes |
|---|---|---|
| **Chat** (home) | The conversation; the composer; suggested follow-ups; the switch pill in the top bar | Answers carry citations and "learn" links (buddy-voice §3). An aside, when allowed, sits apart from the answer and is labeled as one (buddy-voice §6) |
| **Plan card** (inside the chat) | The plan rendered from the plan store: action, targets, expected effect, blast radius, gate results, rollback, expiry, the plan's why (spec §6f, proposed in #26), and the plan ID with the first 12 characters of its hash | Layer 0, no voice. One link, **Review and approve in GitHub**, styled as a link with an external-site icon and the host shown, so it can't be mistaken for an in-app approval; it opens the approve run. The card tells the operator to check that GitHub shows the same plan ID and hash. Hard steady mode: no aside |
| **Status** (from the switch pill) | Kill switch position and what it means right now; today's token use against the cap; anything waiting on the operator (plans, spend alerts) | Each item links back into the conversation it came from. Not a dashboard |
| **History** (drawer) | The operator's own past conversations, newest first, fetched from the server | Titles are generated server-side and pseudonymized like the transcript (spec §6b); nothing is stored on the device |
| **Settings** | Humor on or off (buddy-voice `humor: off`), answer depth (short or longer), theme (system, light, dark), notifications (§7), sign out | Kept in `localStorage`; none of it is estate data |
| **Install hint** | One card after the first finished answer in a browser tab, never before it, platform-specific (§5) | Dismissed for good with one tap |
| **Offline** | "You're offline. Nothing was sent." and the last screen, read-only | No queued messages (rule 6) |
| **Halted** | `halt-agents`: no composer; status, the reason if known, and Cloud Shell blocks (governance §2.3). `halt-all`: a static notice served by the static host, because the API is stopped | Hard steady. The app can't tell `halt-all` from a network failure, so it says what it knows: "avd-buddy can't reach its service." |

**Top bar.** On a phone it holds three things only: the avatar and "avd-buddy" (opens Settings), the switch pill (opens Status), and a menu button (History, Settings, sign out). Nothing else competes with the conversation.

**The research-only notice** (design system rule: the notice comes first) shows on the sign-in screen, at the top of an empty conversation, and in Settings, in the design system's `.warn` box.

**The kill switch in the app.** The switch pill is always visible, always with its position as text, never color alone: "run" with `--good` in normal operation; `halt-actions` with `--caution`; `halt-agents` and `halt-all` with `--bad`, and those three turn the pill into a banner under the top bar that stays until the position changes. Governance §2.3 allows a person to trip the switch "from a button in avd-buddy": the app shows that button on the Status screen, behind a confirmation that names the position, and calls the same trip path step 3 builds. It never offers a reset; resetting is a person's Cloud Shell block (governance §2.3, R6).

**Teaching links.** Learn links open in the system browser (`target="_blank" rel="noopener noreferrer"`), so a standalone app doesn't trap the operator on Microsoft Learn with no back button.

## 4. Sign-in with Entra

**Use Static Web Apps' built-in auth with a custom Entra provider, not MSAL.js.** Spec §6 already chose it (custom app registration, this tenant only, assignment required, one operators group, Conditional Access with phishing-resistant MFA and a short session). For the installable app it has two more advantages:

- **No token in the browser at all.** Static Web Apps runs the OpenID Connect flow on its side and gives the browser an HTTP-only cookie; the API reads the operator from the `x-ms-client-principal` header the platform adds ([access user information](https://learn.microsoft.com/azure/static-web-apps/user-information)). That keeps spec §3's rule: the API passes the operator's identity as a claim, never a token. MSAL.js would put tokens in browser storage, where any script injection could read them.
- **No client secret either.** The registration trusts a user-assigned managed identity as a federated credential (`OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID`), so the app has no secret to rotate ([custom authentication, "Use a managed identity instead of a secret"](https://learn.microsoft.com/azure/static-web-apps/authentication-custom#configure-a-custom-identity-provider)). Add that identity to the table in [identities.md](identities.md) when this is built: it has no Azure role and no Graph permission.

**The tenant ID problem.** The provider's `openIdIssuer` in `staticwebapp.config.json` contains the tenant ID. That file is generated at deploy time in the adopter's private repo (spec §5b) from a repository variable; the public repo holds a template with `<tenant-id>`. The client ID is an app setting (`clientIdSettingName`), never in the file.

**Installed-app behavior.** Sign-in is a full-page redirect (`/.auth/login/aad`), which works in a home-screen app on iOS and Android; no popups. `/.auth/*` and `/api/*` are excluded from the service worker and from `navigationFallback`. A short Conditional Access session means the operator signs in again often; the app shows a calm "Sign in again to continue" and keeps the draft message in memory (not storage) across the redirect only if the platform allows it, otherwise it's lost and says so.

**If avd-buddy ever leaves Static Web Apps,** use MSAL.js with the authorization code flow and PKCE, redirect only, and an in-memory cache ([MSAL.js SPA guidance](https://learn.microsoft.com/entra/identity-platform/scenario-spa-overview)). That is a design change and needs its own red team pass.

## 5. Installable

| Piece | Choice |
|---|---|
| Manifest | `name` "avd-buddy", `short_name` "buddy", `display` `standalone`, `start_url` `/`, `scope` `/`, `id` `/`, `background_color` and `theme_color` from avd-ops tokens (`--brand-ink` dark, `--bg` light; JSON can't read CSS variables, so a test checks the hex equals the token); `<meta name="theme-color">` twice with `media` for light and dark, so the status bar matches the theme; icons from §6 |
| Service worker | Precaches the shell (HTML, CSS, JS, fonts, icons) under a versioned cache name; network-only for `/api/*` and `/.auth/*`; deletes old caches on activate; never caches a response that carries estate data. A new version shows "Update ready, reload" rather than reloading mid-conversation |
| Android, Chromium | The browser's own install prompt, plus an in-app Install button driven by `beforeinstallprompt` ([MDN: making PWAs installable](https://developer.mozilla.org/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable)) |
| iPhone, iPad | No install event exists. The install hint shows the two steps: Share, then Add to Home Screen. Shown once, in Safari only, never inside the installed app |
| Splash | Android builds it from the manifest's name, icon and `background_color`. iOS shows `background_color`; per-device `apple-touch-startup-image` files are left out until someone asks for them |
| Phone layout | `viewport-fit=cover` with `env(safe-area-inset-*)` padding, so the composer clears the home indicator and the notch; `interactive-widget=resizes-content`, so the on-screen keyboard never covers the composer; the composer stays pinned to the bottom |
| iPad | The same app; at 768 px and wider, history stays open as a side panel. Hardware keyboard: Enter sends, Shift+Enter adds a line |

## 6. The icon, as a swappable asset

### 6.1 One source, one setting

The build reads one source file named in `buddy/app/brand.json` (`"icon_source": "placeholder"` or `"mascot"`) and generates every size in [the asset README's table](../assets/buddy/README.md#making-the-sizes). Pages and the manifest reference the generated names only, so swapping the icon is a one-line change and a rebuild, with no edit to HTML, CSS or the manifest. An adopter can point the setting at their own file.

### 6.2 The owner's icon, by an accepted-risk decision

The owner's icon (`assets/buddy/avd-buddy-icon-source.jpg`) resembles Microsoft's Clippy. On 2026-10-20 the owner chose to use it anyway, for an open source research project, with the risk explained; the decision and its words are in [`assets/buddy/REVIEW.md`](../assets/buddy/REVIEW.md). So:

- `icon_source` is `mascot`: the app icon and the chat avatar come from the owner's icon, through the SVG redraw in the asset README.
- The `placeholder` stays buildable: the avd-ops mark (`brand/mark.svg`, vendored with the design system) on a `--brand-ink` tile, with a small chat bubble, at the same sizes. Going back is the one-line change in §6.1, plus a line in `REVIEW.md`.
- A test fails the build if `icon_source` is `mascot` and `assets/buddy/REVIEW.md` doesn't exist, so the icon can't be used without a recorded decision. Only the owner's own word changes that file.
- The icon stays off avd-ops.dev and the other family pages until the owner says otherwise.

## 7. Push notifications: later, opt-in, and empty

Feasible, with limits, and **not in step 2**. There's nothing to approve until step 3, and push is the one place the app would talk to another company's servers.

- **What can push:** a plan waiting for this operator's approval; the kill switch leaving `run`; a spend alert. Never chat replies, never marketing, never "we miss you".
- **What a push carries:** a fixed text, "avd-buddy needs you", and nothing else: no plan ID, action, name, count or estate word. Push services (Apple, Google, Mozilla) relay the message, encrypted, but they still see that it was sent and when. Tapping it opens the app, which signs in and fetches what's waiting.
- **Off by default.** The operator turns it on in Settings with a tap (browsers require a user gesture). Signing out unsubscribes.
- **iPhone and iPad:** web push works only for an app added to the Home Screen, on iOS and iPadOS 16.4 or later ([WebKit: web push for web apps on iOS and iPadOS](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)). Reports of iOS deliveries stopping without notice mean push is a convenience, never the only signal: the GitHub notification for the approve run (spec §5) stays the one that counts.
- **The VAPID private key** that signs pushes is a secret: it lives in Key Vault and the API reads it with its managed identity. Nobody pastes it anywhere.
- **The sender** is a function in the API, triggered by the plan store and the switch flag; it never runs at `halt-all`, since the API is stopped.

**Done when** a spike (estimated first, in the [spend.md](spend.md) format) shows a push arriving on an installed iPhone, iPad and Android phone with nothing in its payload but the fixed text.

## 8. Privacy and security

- **Content security policy** in `staticwebapp.config.json`: `default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`, plus `Referrer-Policy: no-referrer` and `Permissions-Policy` denying camera, microphone and location. No inline script or style (the design system already bans inline styles).
- **Rendering untrusted text.** Agent answers, tool output and quoted evidence are untrusted (spec §6). They render as text through a small Markdown subset with no raw HTML; links show their full host before the operator opens them.
- **No analytics, no telemetry, by default and with no switch to turn them on.** The only signals the client sends are the conversation and the explicit thumbs up or down (spec §6b). Behavior signals (rephrasing, abandoned plans) are computed on the server from the transcript.
- **Pseudonyms stay server-side.** The client never receives a mapping table; it shows what the API returns, which is already redacted the way spec §6 requires.
- **Storage.** `localStorage` holds settings only. No conversation, plan or name goes into `localStorage`, `sessionStorage`, IndexedDB or the Cache API. A test scans every store for tokens, GUIDs and the fixtures' estate names (§12).
- **Sign out** calls `/.auth/logout`, clears the caches, unregisters push and returns to the sign-in screen.
- **Shared devices.** The app is for an operator's own phone or tablet. On a shared one, the short session and sign-out are the protection; the install hint says so.

## 9. Hosting, performance and accessibility

**Hosting.** Azure Static Web Apps, Standard plan, in the swarm's own resource group (spec §3, §6), deployed from the adopter's private repo. It needs nothing from the landing zone and keeps working, and saying so, when the landing zone is down or deleted. Two facts from the [API constraints](https://learn.microsoft.com/azure/static-web-apps/apis-overview) shape the API:

- **Bring your own Functions, not managed functions.** The API needs a managed identity (to call the coordinator and read Key Vault), which managed functions don't have ([comparison](https://learn.microsoft.com/azure/static-web-apps/apis-functions)). Linking one needs the Standard plan, which spec §6 already requires.
- **HTTP only, 45 seconds per request.** No WebSockets, and an agent answer can take longer. So a turn is short requests: `POST /api/turns` returns a turn ID, and the client long-polls `GET /api/turns/{id}?after=n` (each poll under 30 seconds) for new chunks until the turn ends. It works through the platform's proxy and on flaky mobile networks, and a dropped poll just retries.

GitHub Pages or the avd-ops site can't host it: neither offers the sign-in or the `/api` proxy, and avd-ops.dev stays a public site with no estate behind it.

**Performance budgets** (checked in CI on a throttled mid-range phone profile):

| Measure | Budget |
|---|---|
| Shell JavaScript, compressed | 60 KB or less; no framework unless the evaluation in §11 picks one within budget |
| Total first load, compressed (fonts included) | 300 KB or less |
| Largest Contentful Paint, cold, slow 4G | 2.5 s or less; installed and warm: 1 s or less |
| Time from Send to the first visible sign of work | 200 ms or less (local echo and a "thinking" line) |

**Accessibility:** WCAG 2.2 AA, checked by tools and by hand.

- New answer text is announced once it's complete, through a polite live region, not chunk by chunk.
- The aside is labeled "aside" for screen readers and carries nothing an operator needs (buddy-voice §7).
- The plan card is a definition list with headings, readable in order by VoiceOver and TalkBack, and every gate result has a text label, not just a color.
- Touch targets are at least 44 by 44 px; the layout holds at 200% text size and 320 px width; motion follows `prefers-reduced-motion`; focus is always visible (the design system's `--focus`).

## 10. Voice and calm

The mode comes from code, as in [buddy-voice.md](buddy-voice.md) §4; the app only renders it.

- **Hard steady screens:** plan card, Status at any position but `run`, Halted, any security finding. No aside, no animation, no suggested follow-ups that aren't next steps.
- **Soft steady:** a failed run, a spend alert, the operator's stress words. The UI is the same as light; only the text changes.
- **Copy owned by the app** (errors, empty states, the install hint, sign-in) follows the same voice: answer first, never blame, say what's safe. Example: "Couldn't reach avd-buddy. Nothing was sent. Try again when you're back online."

## 11. Design

The app uses the avd-ops design system ([docs/design-system.md](https://github.com/avd-ops/avd-ops/blob/main/docs/design-system.md)), vendored at a tagged version into `buddy/app/assets/` like the landing zone's portal, never loaded from avd-ops.dev at run time.

- Colors only through tokens; dark mode follows the system, with `data-theme` to force one.
- New components it needs (message, composer, plan card, switch pill, install hint) are listed with their tokens in the build PR. The switch pill is its own component, not the repo `.status.s-*` pills, and uses the result tokens (`--good`, `--caution`, `--bad`), which the design system reserves for results, never decoration. The aside uses `--muted` text with an `.eyebrow` label "aside", and must still pass AA contrast on `--surface` in both themes. Any that another page could use go to avd-ops first, then get vendored.
- **Design pass before UI lands:** the build PR runs the design critique and accessibility review skills against the design system, with screenshots of every screen in §3 at phone and iPad widths, light and dark, and records what changed because of them.
- **Design pass on this spec (2026-10-20),** with the design critique skill against the design system: it moved the install hint after the first answer, renamed the plan card's link so it can't read as an in-app approval, gave the switch pill text and result tokens instead of a color dot, put the research-only notice on the sign-in and empty screens, cut the phone top bar to three items, and added safe-area and keyboard handling. The build PR repeats the pass on real screens.
- **Reuse before building:** evaluate Microsoft's [Bot Framework Web Chat](https://github.com/microsoft/BotFramework-WebChat) (MIT, accessible chat UI) against the budgets in §9 and the design system before writing our own chat view. Open-source chat front ends that install as PWAs (for example [Open WebUI](https://github.com/open-webui/open-webui)) show the pattern works; we take their lessons, not their code or their model-calling backend.

## 12. Testing

Using the web app testing skill (Playwright with the session's Chromium), against a mock API that serves fixtures, no Azure:

1. **Manifest and install:** required fields, icons present at every size, `id` and `scope` correct, the hex colors equal the tokens.
2. **Service worker:** shell precached; offline reload shows the Offline screen; `/api` and `/.auth` never served from cache; an update shows "Update ready".
3. **No requests to other sites:** record every request in a full session (sign-in mocked); any origin but the app's own fails.
4. **CSP present** on every route in `staticwebapp.config.json`, and an injected inline script in a fixture answer doesn't run.
5. **Nothing left on the device:** after a session with plans and names in the fixtures, scan `localStorage`, `sessionStorage`, IndexedDB and every cache for JWT shapes, GUIDs and fixture names; after sign-out, every store is empty but settings.
6. **Front end only:** the plan card renders a fixture plan exactly (snapshot), shows the plan ID and hash prefix, has one link to GitHub and no button that POSTs anything about the plan.
7. **Voice:** a hard steady fixture with an `aside` field renders no aside; `humor: off` hides every aside.
8. **Kill switch:** each position renders its screen; `halt-all` works with the API returning nothing; the trip button needs confirmation; no reset control exists anywhere.
9. **Accessibility:** axe-core with no violations on every screen; keyboard-only run through chat, plan card and settings.
10. **Viewports:** 320, 375 and 393 px phones, 820 px iPad, portrait and landscape; no horizontal scroll.
11. **Budgets:** the §9 table, on a throttled profile.

Lighthouse no longer has a PWA category, so it's used for performance only; the installability checks are the tests above. Before calling a phase done, the owner tries it on a real iPhone, iPad and Android phone: install, sign in, a conversation, a plan card, offline, sign out.

## 13. Phases

| Phase | When | Delivers | Exit |
|---|---|---|---|
| **A. Installable chat** | Build order step 2 | Everything above except push; read-only answers; the switch pill and Halted screens | §12 passes; the owner installs it on two devices and asks the step 2 eval questions from it |
| **B. Plans** | Step 3 | The plan card and the GitHub link, the trip button | One of step 3's ten approved changes started from the app on a phone |
| **C. Push** | After step 3, if the owner wants it | §7, after its spike | §7's done line |

## 14. Prior art

Linked, not restated:

- PWAs: [MDN Progressive web apps](https://developer.mozilla.org/docs/Web/Progressive_web_apps), [web.dev Learn PWA](https://web.dev/learn/pwa/), [maskable icons](https://web.dev/articles/maskable-icon).
- Sign-in: [Static Web Apps authentication](https://learn.microsoft.com/azure/static-web-apps/authentication-authorization), [custom Entra provider with a managed identity](https://learn.microsoft.com/azure/static-web-apps/authentication-custom), [user information](https://learn.microsoft.com/azure/static-web-apps/user-information); MSAL.js only as the fallback in §4.
- Hosting: [API constraints](https://learn.microsoft.com/azure/static-web-apps/apis-overview), [bring your own Functions](https://learn.microsoft.com/azure/static-web-apps/functions-bring-your-own).
- Push on Apple devices: [WebKit's announcement](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/).
- Approvals: [GitHub, reviewing deployments](https://docs.github.com/actions/how-tos/deploy/configure-and-manage-deployments/review-deployments).
- Chat UI: [Bot Framework Web Chat](https://github.com/microsoft/BotFramework-WebChat); [Open WebUI](https://github.com/open-webui/open-webui) as an example of an installable chat front end.
- The family's own: the landing zone portal's no-other-sites promise and the avd-ops design system; [buddy-voice.md](buddy-voice.md).

What we found no prior art for, and so wrote: a chat PWA that by rule can't approve the changes it proposes, renders the plan from a store it can't write, and keeps no estate data on the device.

## 15. Open questions

- **BQ1** ~~The trademark review of the icon~~ **Answered (2026-10-20):** the owner chose the icon as an accepted risk (§6.2, `assets/buddy/REVIEW.md`). Revisit on an objection or before any use someone might pay for.
- **BQ2** Does GitHub Mobile let a reviewer approve a deployment on a phone? GitHub's docs describe the web flow only. Check on a real device in phase B; if not, the "Review and approve in GitHub" link opens the mobile web page, which works.
- **BQ3** Push (phase C) at all? Default: only if the owner asks after phase B.
- **BQ4** Web Chat or our own chat view (§11)? Default: our own, unless Web Chat fits the budgets.
- **BQ5** A custom domain for the adopter's app? Default: the Static Web Apps default host; never a subdomain of avd-ops.dev.
