#!/usr/bin/env python3
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Checks GitHub Actions workflow files against avd-swarm's workflow rules (spec section 5b, section 6b; governance section 2.4).

W1  A public repository holds no write-path workflow: no job environment, no `id-token: write`
    (or `write-all`), no Azure login step.
W2  A step that opens a pull request uses a GitHub App installation token from
    actions/create-github-app-token in the same job, never GITHUB_TOKEN or github.token:
    events caused by the default token start no workflows, so the guard would never run.
W3  No `pull_request_target` trigger.
W4  No `${{ github.event.* }}` or `${{ github.head_ref }}` inside a `run:` script; pass event text through `env:`.
W5  A workflow started by an untrusted event (issues, comments, reviews, discussions, workflow_run,
    pull_request) asks for no `id-token: write` and reads no secret other than GITHUB_TOKEN.

Usage: check_workflows.py [--public | --private] [FILE ...]
Without files it checks .github/workflows/*.yml and templates/workflows/*.yml (W1 doesn't apply to
templates, which GitHub doesn't run). Visibility comes from the flag, else the REPO_PRIVATE
environment variable ("true" or "false"); unknown counts as public. Exit 1 on any finding.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml

UNTRUSTED_EVENTS = {
    "issues", "issue_comment", "pull_request", "pull_request_review", "pull_request_review_comment",
    "discussion", "discussion_comment", "workflow_run",
}
EVENT_IN_RUN = re.compile(r"\$\{\{\s*github\.(event\.|head_ref)")
DEFAULT_TOKEN = re.compile(r"secrets\.GITHUB_TOKEN|github\.token")
SECRET_REF = re.compile(r"secrets\.([A-Za-z0-9_]+)")
APP_TOKEN = re.compile(r"\$\{\{\s*steps\.([A-Za-z0-9_-]+)\.outputs\.token\s*\}\}")
OPENS_PR_RUN = re.compile(r"\bgh\s+pr\s+create\b|\bgh\s+api\b[^\n]*/pulls\b|pulls\.create|createPullRequest")
PR_ACTIONS = ("peter-evans/create-pull-request",)
APP_TOKEN_ACTION = "actions/create-github-app-token"


def triggers(workflow: dict) -> set[str]:
    # PyYAML reads the bare key `on` as the boolean True.
    on = workflow.get("on", workflow.get(True))
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return set(on)
    if isinstance(on, dict):
        return set(on)
    return set()


def grants_id_token(permissions) -> bool:
    if permissions == "write-all":
        return True
    return isinstance(permissions, dict) and permissions.get("id-token") == "write"


def step_token(step: dict, job: dict, workflow: dict) -> str | None:
    """The token a PR-opening step uses: its `with.token`/`github-token`, else GH_TOKEN/GITHUB_TOKEN env, nearest scope first."""
    with_ = step.get("with") or {}
    for key in ("token", "github-token"):
        if key in with_:
            return str(with_[key])
    for scope in (step, job, workflow):
        env = scope.get("env") or {}
        for key in ("GH_TOKEN", "GITHUB_TOKEN"):
            if key in env:
                return str(env[key])
    return None


def opens_pr(step: dict) -> bool:
    uses = str(step.get("uses", ""))
    if uses.split("@")[0] in PR_ACTIONS:
        return True
    script = str(step.get("run", "")) + str((step.get("with") or {}).get("script", ""))
    return bool(OPENS_PR_RUN.search(script))


def check(path: str, text: str, public: bool, template: bool = False) -> list[str]:
    findings: list[str] = []
    try:
        workflow = yaml.safe_load(text) or {}
    except yaml.YAMLError as error:
        return [f"{path}: not valid YAML ({error})"]
    if not isinstance(workflow, dict):
        return [f"{path}: not a workflow"]
    events = triggers(workflow)
    jobs = workflow.get("jobs") or {}
    untrusted = bool(events & UNTRUSTED_EVENTS)

    if "pull_request_target" in events:
        findings.append(f"{path}: W3 uses pull_request_target")
    if untrusted and grants_id_token(workflow.get("permissions")):
        findings.append(f"{path}: W5 untrusted trigger with id-token: write")
    if public and not template and grants_id_token(workflow.get("permissions")):
        findings.append(f"{path}: W1 id-token: write in a public repository")
    if untrusted:
        for name in sorted(set(SECRET_REF.findall(text)) - {"GITHUB_TOKEN"}):
            findings.append(f"{path}: W5 untrusted trigger reads secret {name}")

    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        where = f"{path}: job {job_id}"
        if public and not template:
            if "environment" in job:
                findings.append(f"{where}: W1 uses environment {job['environment']!r} in a public repository")
            if grants_id_token(job.get("permissions")):
                findings.append(f"{where}: W1 id-token: write in a public repository")
        if untrusted and grants_id_token(job.get("permissions")):
            findings.append(f"{where}: W5 untrusted trigger with id-token: write")
        steps = [s for s in (job.get("steps") or []) if isinstance(s, dict)]
        app_steps = {s.get("id") for s in steps if str(s.get("uses", "")).split("@")[0] == APP_TOKEN_ACTION}
        for index, step in enumerate(steps):
            label = f"{where} step {step.get('name') or step.get('id') or index + 1}"
            uses = str(step.get("uses", "")).split("@")[0]
            if public and not template and uses == "azure/login":
                findings.append(f"{label}: W1 Azure login in a public repository")
            if EVENT_IN_RUN.search(str(step.get("run", ""))):
                findings.append(f"{label}: W4 event text interpolated into run:")
            if opens_pr(step):
                token = step_token(step, job, workflow)
                match = APP_TOKEN.search(token or "")
                if token is None or DEFAULT_TOKEN.search(token):
                    findings.append(f"{label}: W2 opens a pull request with the default token; use a GitHub App token")
                elif not match or match.group(1) not in app_steps:
                    findings.append(f"{label}: W2 opens a pull request with a token that isn't from {APP_TOKEN_ACTION} in this job")
    return findings


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if "--private" in argv:
        public = False
    elif "--public" in argv:
        public = True
    else:
        public = os.environ.get("REPO_PRIVATE", "").strip().lower() != "true"
    root = Path(__file__).resolve().parent.parent
    files = [Path(a) for a in args] or sorted(
        list((root / ".github/workflows").glob("*.y*ml")) + list((root / "templates/workflows").glob("*.y*ml")))
    findings: list[str] = []
    for file in files:
        template = "templates" in file.resolve().parts
        findings += check(str(file), file.read_text(encoding="utf-8"), public, template)
    for finding in findings:
        print(finding)
    print(f"{len(files)} workflow file(s) checked as {'public' if public else 'private'}: {len(findings)} finding(s).")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
