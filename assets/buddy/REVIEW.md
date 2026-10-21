# avd-buddy's icon: the owner's decision

- **Date:** 2026-10-20
- **Decision:** use the Gemini-made icon (`avd-buddy-icon-source.jpg`) as avd-buddy's chat avatar and app icon now, without a formal trademark review. **Accepted risk.**
- **The owner's words:** "its an open source research project tho so for now i want to stay with the one gemini made and see what happens"
- **The risk, as explained to the owner before deciding:** the character resembles Microsoft's Clippy. If a trademark holder objects, the icon has to be replaced, and because this repo is public, removing it means rewriting `main`'s history.
- **Scope:** avd-buddy only (`buddy/app/brand.json` sets `"icon_source": "mascot"`). avd-ops.dev and the other family pages keep the avd-ops mark until the owner says otherwise.
- **Revisit:** if anyone objects, if the project stops being a research project, or before any use that someone might pay for. Then switch `icon_source` back to `placeholder` (one line) and record the change here.
