# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Tests for .claude/skills/sandbox/SKILL.md and docs/sandbox-skill.md (avd-swarm#47)."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = ROOT / ".claude/skills/sandbox/SKILL.md"
AGENTS_PATH = ROOT / "AGENTS.md"


class SandboxSkillTests(unittest.TestCase):
    def test_skill_exists_and_is_linked_from_agents_md(self):
        self.assertTrue(SKILL_PATH.is_file(), ".claude/skills/sandbox/SKILL.md must exist")
        agents_text = AGENTS_PATH.read_text(encoding="utf-8")
        self.assertIn(".claude/skills/sandbox/SKILL.md", agents_text)

    def test_skill_enforces_hard_rules_and_uses_env_var_for_companion_repo(self):
        text = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("go batch <id> $<total>", text)
        self.assertIn("-DiscardLastCopy", text)
        self.assertIn("AVD_PRIVATE_REPO", text)
        self.assertIn("https://github.com/avd-ops/avd-landing-zone/blob/master/.claude/skills/sandbox-batch/SKILL.md", text)
        # Must not name any private repository slug
        for match in re.finditer(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", text):
            slug = match.group(1)
            self.assertTrue(slug.startswith("avd-ops/"), f"unexpected repository reference in skill: {slug}")

    def test_every_relative_link_in_skill_resolves(self):
        text = SKILL_PATH.read_text(encoding="utf-8")
        for match in re.finditer(r"\]\(([^)]+)\)", text):
            target = match.group(1).split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (SKILL_PATH.parent / target).resolve()
            self.assertTrue(resolved.exists(), f"broken relative link in sandbox skill: {target} -> {resolved}")

    def test_security_policy_and_sha_pinned_workflows_exist(self):
        self.assertTrue((ROOT / "SECURITY.md").is_file())
        self.assertTrue((ROOT / ".github/workflows/secrets.yml").is_file())
        for wf in list((ROOT / ".github/workflows").glob("*.yml")) + list((ROOT / "templates/workflows").glob("*.yml")):
            txt = wf.read_text(encoding="utf-8")
            for m in re.finditer(r"^\s*-?\s*uses:\s*([^\s#]+)", txt, flags=re.MULTILINE):
                self.assertRegex(m.group(1), r"@[0-9a-f]{40}$", f"{wf.name} has unpinned action: {m.group(1)}")


if __name__ == "__main__":
    unittest.main()
