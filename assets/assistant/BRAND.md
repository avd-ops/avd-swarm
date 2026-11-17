# `avd-assistant` (Assistant) — Brand & Icon Identity Specification

**Parent Ecosystem:** `avd-ops` Design System 3.0 ("Quiet Instrument")  
**Ethos & Tagline:** `"Deploy quietly"`  
**Canonical Endpoint:** `https://assistant.avd-ops.dev`  
**Short UI / Home Screen Name:** `Assistant`

---

## 1. Visual DNA & Color System

`avd-assistant` avoids cartoon mascots, paperclips, glowing AI brains, purple gradients, and 3D bevels. Every asset is constructed from flat, crisp geometric vector primitives engineered on a `16px` modular sub-grid (`512 = 32 × 16`).

| Token | Hex | Role | Contrast vs. `#0d1015` |
| :--- | :--- | :--- | :--- |
| **Primary Teal** | `#1ca998` | Shield body, active mode accents, prompt sigils | `6.52 : 1` (AA Large / UI) |
| **Deep Ink** | `#0d1015` | PWA tile canvas, recessed visor cavity, light-mode strokes | `1.00 : 1` (Canvas) |
| **Light Surface** | `#f4f6f9` | Light-theme documentation & header lockup surface | `16.70 : 1` |
| **Pure White** | `#ffffff` | High-contrast `><` visor chevrons & dark-theme wordmark | `19.10 : 1` (AAA) |
| **Cool Slate** | `#64748b` | `1px` architectural hairlines, coordinate grids, muted mono metadata | `4.55 : 1` |

---

## 2. Core Mark Geometry: Shield + `><` Visor

- **Architectural Shield Silhouette:** Constructed with an exact **3:1 crown pitch** (`V0(256,80) → V1(400,128)`), parallel vertical flank rails (`x = 112` and `x = 400`), and an exact **`45.0°` lower keel** (`dx = 144, dy = 144` to `V3(256,432)`), finished with `10px` quadratic vertex fillets to eliminate miter spikes across DPI scales.
- **80% Maskable Safe Zone Compliance:** All shield vertices on `icon-pwa-maskable.svg` sit within radius `r = 192.7px` of `(256, 256)`—comfortably inside the W3C `80%` maskable safe circle (`r = 204.8px`) for Android circles, squircles, teardrops, and iOS home screen icons.
- **Machined `2:1` Tactical Visor & `><` Prompt:** The recessed `#0d1015` visor (`224×112` on the safe-zone mark) features `8px` upper chamfers and `24px` `45°` lower chamfers echoing the shield keel. Inside, closed-polygon `#ffffff` `><` chevrons serve simultaneously as focused operator eyes, bidirectional AVD remote-session glyphs, and an interactive terminal prompt, separated by a `48px` (`3.0px` at `32×32`) center channel so they never collide at small sizes.
- **Single-Path `evenodd` Monochrome Stencil:** `icon-monochrome-white.svg` and `icon-monochrome-ink.svg` combine Shield (`winding 1 = opaque`), Visor (`winding 2 = transparent knockout`), and `><` Chevrons (`winding 3 = opaque`) in a single `<path fill-rule="evenodd">` element for native Android 13+ themed icons.

---

## 3. Simplified 32px Favicon & Chat Avatar

At `16px–32px`, `favicon-32.svg` and `avatar-chat.svg` strip away the tall lower shield keel and zoom directly into the **chamfered Teal-rimmed Visor + oversized White `><` chevrons**, maintaining a `2.0px` clear gap between chevron vertices even at `16×16`.

---

## 4. How the 4 Mode Sub-Badges Tie Back to the Primary Mark

Each `24×24` UI mode icon is derived by **isolating and transforming one structural primitive of the parent Shield + `><` Visor**, giving every mode a **100% distinct outer silhouette** in single-color monochrome:

1. **Chief of Staff (`mode-chief-of-staff.svg` — *Triage / Priority / Command*):** Rotates the visor's `45°` `>` chevron `90°` upward into twin stacked command chevrons (`^^`) echoing the shield's peaked crown, anchored above a horizontal P1 triage bar. *Silhouette: Tall pointed double-wing spire.*
2. **Red Team (`mode-red-team.svg` — *Challenge / Risk / Inspection*):** Mirrors the shield's `45°` lower keel into an adversarial `45°` diamond reticle pierced by four protruding cardinal crosshair ticks and a central inspection lock-pip. *Silhouette: 4-point spiked cardinal crosshair diamond.*
3. **Guru (`mode-guru.svg` — *Architecture / Knowledge / Deep Dive*):** Expands the shield's parallel armor planes into a 3-tier isometric control-plane stack representing AVD Foundation, Control Plane, and Session Host strata. *Silhouette: Wide stepped 3-layer hexagonal stack.*
4. **Buddy (`mode-buddy.svg` — *Warmth / Approachable / Dry-Wit Companion*):** Lifts the chamfered horizontal visor capsule straight out of the armored shield ("armor off") and relaxes the inward `><` session chevrons into two crisp, straight-edged `45°` upward `^ ^` companion eyes—warm and human with zero cartoon curves. *Silhouette: Compact low-slung landscape visor capsule.*

---

## 5. Package Inventory

- `svg/icon-pwa-maskable.svg` — `512×512` full-bleed `#0d1015` maskable PWA icon (80% safe zone)
- `svg/icon-pwa-rounded.svg` — `512×512` rounded squircle dock/app tile
- `svg/icon-mark-transparent.svg` — Transparent-background shield + `><` visor mark for web headers
- `svg/icon-monochrome-white.svg` — Single-color white alpha stencil (`fill-rule="evenodd"`) for Android themed icons
- `svg/icon-monochrome-ink.svg` — Single-color `#0d1015` ink alpha stencil (`fill-rule="evenodd"`)
- `svg/favicon-32.svg` — Stripped-down `32×32` high-contrast visor + `><` favicon
- `svg/avatar-chat.svg` — `32×32` rounded `#0d1015` chat stream avatar
- `svg/mode-chief-of-staff.svg` — `24×24` Chief of Staff mode sub-badge
- `svg/mode-red-team.svg` — `24×24` Red Team mode sub-badge
- `svg/mode-guru.svg` — `24×24` Guru mode sub-badge
- `svg/mode-buddy.svg` — `24×24` Buddy mode sub-badge
- `svg/lockup-dark.svg` — Horizontal `avd-assistant` wordmark lockup (Dark `#0d1015`)
- `svg/lockup-light.svg` — Horizontal `avd-assistant` wordmark lockup (Light `#f4f6f9`)
- `svg/social-card-1200x630.svg` — `1200×630` Open Graph / GitHub social banner (`assistant.avd-ops.dev`)
- `png/` — Exact pixel rasterizations (`icon-pwa-512.png`, `icon-pwa-192.png`, `apple-touch-icon-180.png`, `favicon-32.png`, `favicon-16.png`, `favicon.ico`, `social-card-1200x630.png`)
- `pwa/site.webmanifest` — Installable PWA manifest configured for `avd-assistant` (`Assistant`)
- `tokens/` — CSS custom properties and W3C JSON design tokens
