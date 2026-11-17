# Snippet allowlist (`catalog/snippets/`)

> **Personal research project — not for production.** Provided as is, without warranty; not affiliated with the author's employer, Liquidware or Microsoft.

Protected path (`docs/assistant-spec.md` §4, Fable finding `F1`).

`avd-assistant` (Guru and Buddy modes) only renders a code block as runnable/copyable if its exact `sha256` appears in [`allowlist.yml`](allowlist.yml). Any other code block from any indexed source renders as quoted text labeled **"unverified, not for pasting"** with no copy button.
