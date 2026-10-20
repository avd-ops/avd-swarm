# avd-buddy's icon

`avd-buddy-icon-source.jpg` is the source image for avd-buddy's chat avatar and its installable web app icon ([docs/buddy-app.md](../../docs/buddy-app.md) §6). The owner chose it on 2026-10-20.

- **What it is:** a teal paperclip character with eyes, beside the avd-ops shield (desktop, cloud and the >< of a remote session), on a dark rounded tile. 2048 x 2048 JPEG, sRGB.
- **Where it came from:** made with Google's Gemini image model. The file keeps its C2PA content credentials as Google wrote them ("Created by Google Generative AI", an imperceptible SynthID watermark); don't strip them. They carry no personal data.
- **Kept byte for byte.** SHA-256 `eabcf7d6e58554e0c6f3cc3e5637495c2fdc0d288898ccfc54da4788dbbcd920`. Edits go in a new file, never over this one.

## Public use waits for a trademark review

The character resembles Microsoft's Clippy, the Office assistant. Until a trademark review says it can be used, treat it as **not cleared for public use**:

- avd-buddy's builds use the **placeholder** icon (docs/buddy-app.md §6.2), and the swap to this one is a one-line PR after the review.
- It doesn't go on avd-ops.dev or any other family page until the owner says so.
- If the review goes badly, deleting the file isn't enough: it stays in this public repo's history, so removing it means rewriting history on `main` and asking forks to do the same. The owner accepted that when asking to commit it.

Generative AI output may also not be protected by copyright in the US, so the image may give us less exclusive control than a drawn mark would. The avd-ops mark faced the same question and was rebuilt as vector art; the same is the next step here (below).

## Making the sizes

Never ship this JPEG to a browser: it's 1.2 MB, it has the tile's frame and outer margin baked in, and every platform adds its own mask. Derive the app's icons from a cleaned-up source instead:

1. **Redraw as SVG** (`avd-buddy-icon.svg`, next to this file): the character and shield only, no tile, no frame, no outer margin, colors from avd-ops tokens (`--brand` #1ca998 teal, `--brand-ink` #0d1015 ink). This is the swappable source the build reads.
2. **Generate** from the SVG, at build time, into `buddy/app/icons/` (git-ignored output):

| File | Size | Rule |
|---|---|---|
| `icon-192.png`, `icon-512.png` | 192, 512 | `purpose: any`; transparent background allowed |
| `icon-maskable-512.png` | 512 | `purpose: maskable`; full-bleed `--brand-ink` background, artwork inside the central 80% safe zone ([web.dev: maskable icons](https://web.dev/articles/maskable-icon)) |
| `icon-monochrome-512.png` | 512 | `purpose: monochrome`; one color with alpha, for Android themed icons |
| `apple-touch-icon.png` | 180 | No transparency (iOS fills it with black); full-bleed background; iOS rounds the corners itself |
| `favicon.svg`, `favicon-32.png` | any, 32 | Character's head only; the full art doesn't read at 32 px |
| `avatar.svg` | any | Chat avatar beside avd-buddy's messages; decorative (`alt=""`), since the name is already shown |

3. **Check** each with the platform's mask preview (Chrome DevTools, Application, Manifest) and on a real iPhone, iPad and Android home screen before calling it done.
