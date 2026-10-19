# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Tests for scripts/check_workflows.py (spec section 5b, section 6b; red team R17, R20)."""

import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_workflows  # noqa: E402

IMPROVE_DEFAULT_TOKEN = """
name: Improve
on:
  schedule: [{cron: '0 3 * * 1'}]
permissions:
  contents: write
  pull-requests: write
jobs:
  improve:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Open the draft PR
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: gh pr create --draft --title "Improve" --body-file body.md
"""

IMPROVE_APP_TOKEN = """
name: Improve
on:
  schedule: [{cron: '0 3 * * 1'}]
permissions:
  contents: read
jobs:
  improve:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - id: app
        uses: actions/create-github-app-token@v2
        with:
          app-id: ${{ vars.IMPROVE_APP_ID }}
          private-key: ${{ secrets.IMPROVE_APP_KEY }}
      - name: Open the draft PR
        env:
          GH_TOKEN: ${{ steps.app.outputs.token }}
        run: gh pr create --draft --title "Improve" --body-file body.md
"""

APPROVE = """
name: Approve
on:
  workflow_dispatch:
    inputs: {planId: {required: true}}
permissions:
  contents: read
  id-token: write
jobs:
  apply:
    runs-on: ubuntu-latest
    environment: approve
    steps:
      - uses: azure/login@v2
        with: {client-id: '${{ vars.WRITE_CLIENT_ID }}', tenant-id: '${{ vars.TENANT_ID }}', subscription-id: '${{ vars.SUB_ID }}'}
"""


def check(text, public=True, template=False):
    return check_workflows.check("wf.yml", textwrap.dedent(text), public, template)


class WorkflowRules(unittest.TestCase):
    def test_w2_default_token_pr_fails(self):
        # A PR opened with GITHUB_TOKEN starts no workflows, so the guard would never run on it (R20).
        findings = check(IMPROVE_DEFAULT_TOKEN, public=False)
        self.assertTrue(any("W2" in f for f in findings), findings)

    def test_w2_github_token_expression_and_missing_token_fail(self):
        self.assertTrue(any("W2" in f for f in check(IMPROVE_DEFAULT_TOKEN.replace("secrets.GITHUB_TOKEN", "github.token"), public=False)))
        no_env = IMPROVE_DEFAULT_TOKEN.replace("        env:\n          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n", "")
        self.assertTrue(any("W2" in f for f in check(no_env, public=False)))

    def test_w2_app_token_passes(self):
        self.assertEqual(check(IMPROVE_APP_TOKEN, public=False), [])

    def test_w2_token_must_come_from_an_app_token_step_in_the_job(self):
        text = IMPROVE_APP_TOKEN.replace("steps.app.outputs.token", "steps.other.outputs.token")
        self.assertTrue(any("W2" in f for f in check(text, public=False)))
        text = IMPROVE_APP_TOKEN.replace("${{ steps.app.outputs.token }}", "${{ secrets.SOME_PAT }}")
        self.assertTrue(any("W2" in f for f in check(text, public=False)))

    def test_w2_create_pull_request_action_without_token_fails(self):
        text = """
        on: {schedule: [{cron: '0 3 * * 1'}]}
        jobs:
          j:
            runs-on: ubuntu-latest
            steps:
              - uses: peter-evans/create-pull-request@v7
        """
        self.assertTrue(any("W2" in f for f in check(text, public=False)))

    def test_w2_github_script_pulls_create_with_default_token_fails(self):
        text = """
        on: {schedule: [{cron: '0 3 * * 1'}]}
        jobs:
          j:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/github-script@v7
                with:
                  script: |
                    await github.rest.pulls.create({owner: 'o', repo: 'r', head: 'h', base: 'main', title: 't'})
        """
        self.assertTrue(any("W2" in f for f in check(text, public=False)))

    def test_w1_write_path_fails_in_public_repo_only(self):
        public = check(APPROVE, public=True)
        self.assertTrue(any("W1" in f and "environment" in f for f in public), public)
        self.assertTrue(any("W1" in f and "id-token" in f for f in public), public)
        self.assertTrue(any("W1" in f and "Azure login" in f for f in public), public)
        self.assertEqual(check(APPROVE, public=False), [])

    def test_w1_does_not_apply_to_templates(self):
        self.assertEqual(check(APPROVE, public=True, template=True), [])

    def test_w3_pull_request_target_fails(self):
        text = "on: pull_request_target\njobs: {j: {runs-on: ubuntu-latest, steps: [{run: echo}]}}\n"
        self.assertTrue(any("W3" in f for f in check(text, public=False)))

    def test_w4_event_text_in_run_fails_and_env_passes(self):
        bad = """
        on: issues
        permissions: {contents: read}
        jobs:
          j:
            runs-on: ubuntu-latest
            steps:
              - run: echo "${{ github.event.issue.title }}"
        """
        good = bad.replace('- run: echo "${{ github.event.issue.title }}"',
                           '- env: {TITLE: "${{ github.event.issue.title }}"}\n                run: echo "$TITLE"')
        self.assertTrue(any("W4" in f for f in check(bad, public=False)))
        self.assertEqual(check(good, public=False), [])

    def test_w5_untrusted_trigger_with_credentials_fails(self):
        text = """
        on: issue_comment
        permissions: {id-token: write}
        jobs:
          j:
            runs-on: ubuntu-latest
            steps:
              - env: {KEY: "${{ secrets.MODEL_KEY }}"}
                run: echo ok
        """
        findings = check(text, public=False)
        self.assertTrue(any("W5" in f and "id-token" in f for f in findings), findings)
        self.assertTrue(any("W5" in f and "MODEL_KEY" in f for f in findings), findings)


class ThisRepo(unittest.TestCase):
    """The repo's own workflows: the guard runs on every PR (so an App-opened PR triggers it) and passes as public."""

    def test_guard_runs_on_pull_requests_and_holds_both_checks(self):
        import yaml
        guard = yaml.safe_load((ROOT / ".github/workflows/guard.yml").read_text(encoding="utf-8"))
        self.assertIn("pull_request", check_workflows.triggers(guard))
        on = guard.get("on", guard.get(True))
        # No branch or path filter: every PR, whatever it touches, runs the guard.
        self.assertFalse(on["pull_request"])
        jobs = guard["jobs"]
        self.assertIn("check_workflows.py", str(jobs["workflows"]))
        self.assertIn("check_agent_pr.py", str(jobs["agent-pr"]))

    def test_all_workflows_pass_as_public(self):
        files = sorted((ROOT / ".github/workflows").glob("*.y*ml"))
        self.assertTrue(files)
        for file in files:
            self.assertEqual(check_workflows.check(str(file), file.read_text(encoding="utf-8"), public=True), [], file.name)


if __name__ == "__main__":
    unittest.main()
