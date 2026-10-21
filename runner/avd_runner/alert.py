# avd-swarm alert job: alert-triggered runbooks. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""The Container Apps Job behind alert-triggered runs (core-and-adapters.md WP6; spec section 3c).

The path is alert rule, action group, Consumption Logic App, storage queue, this job. The queue message holds
an alert ID and nothing else; the job trusts nothing in it beyond that shape. It reads the kill switch first,
reads the alert back from Azure Monitor with its own read-only managed identity, and asks the agent API to
run every read-only runbook whose `trigger.alert` pattern matches the alert rule's name, with
`startedBy: alert`. Only a fired alert starts a run; a resolved one is dropped. Nothing here holds a
GitHub credential, a key or a model credential: every hop is a managed identity.

Each message is deleted once handled, whatever the outcome, so a bad message can't keep the job (and its
bill) running: a malformed message or an unreadable alert is logged and dropped, and so is everything while
the switch is at halt-agents or above. Only an unreadable flag keeps a message, hidden for the visibility
timeout, and a message seen more than MAX_DEQUEUE times is dropped. Logs carry runbook IDs, outcomes and counts, never a host, user or
resource name (R17).
"""

from __future__ import annotations

import base64
import fnmatch
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable

from . import report
from .api import load_all
from .runbook import Runbook

ALERT_ID = re.compile(
    r"^/subscriptions/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"(/resourceGroups/[A-Za-z0-9._()-]{1,90})?"
    r"/providers/Microsoft\.AlertsManagement/alerts/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE)
ALERTS_API = "2019-05-05-preview"
QUEUE_API = "2021-12-02"
MAX_DEQUEUE = 5
MAX_RUNBOOKS = 4  # per alert, so one message's runs fit the job's replica timeout (templates/alert-path)
ARM = "https://management.azure.com"
STORAGE = "https://storage.azure.com"


class BadMessage(ValueError):
    pass


def alert_id(message_text: str) -> str:
    """The alert ID from a queue message: base64 of the ID, as the Logic App writes it, or the plain ID."""
    text = message_text.strip()
    if not ALERT_ID.match(text):
        try:
            text = base64.b64decode(text, validate=True).decode("ascii").strip()
        except (ValueError, UnicodeDecodeError) as error:
            raise BadMessage("not an alert ID") from error
    if not ALERT_ID.match(text):
        raise BadMessage("not an alert ID")
    return text


@dataclass
class Alert:
    rule: str       # the alert rule's name (the last segment of its resource ID)
    condition: str  # Fired or Resolved


def read_alert(get_json: Callable[[str], dict], alert: str) -> Alert:
    """Reads the alert back from Azure Monitor (Alerts Management), never from the message."""
    body = get_json(f"{ARM}{alert}?api-version={ALERTS_API}")
    essentials = (body.get("properties") or {}).get("essentials") or {}
    rule, condition = essentials.get("alertRule"), essentials.get("monitorCondition")
    if not isinstance(rule, str) or not rule.strip() or not isinstance(condition, str):
        raise ValueError("the alert has no rule or condition")
    return Alert(rule.rstrip("/").rsplit("/", 1)[-1], condition)


def matching(runbooks: dict[str, Runbook], rule: str) -> list[str]:
    """Read-only runbooks whose trigger.alert pattern (a glob, case-insensitive) matches the rule's name."""
    return sorted(rid for rid, rb in runbooks.items()
                  if rb.meta["trigger"].get("runMode") == "read-only" and "alert" in rb.meta["trigger"]
                  and fnmatch.fnmatchcase(rule.lower(), str(rb.meta["trigger"]["alert"]).lower()))


def handle(message_text: str, get_json: Callable[[str], dict], runbooks: dict[str, Runbook],
           run_one: Callable[[str], dict]) -> list[str]:
    """One message: log lines with no names. run_one(runbook_id) asks the agent API with startedBy alert; each
    finding's summary (report.summary: ID, outcome, fact values) goes to the log, and the agent API keeps the
    finding itself for the plan store and avd-buddy. The alert path opens no GitHub issue: it holds no GitHub
    credential, by design (P-3)."""
    try:
        alert = read_alert(get_json, alert_id(message_text))
    except BadMessage:
        return ["dropped a message that isn't an alert ID"]
    except Exception as error:
        return [f"dropped an alert that couldn't be read back ({error_detail(error)})"]
    if alert.condition != "Fired":
        return [f"skipped an alert that is {alert.condition}, not Fired"]
    ids = matching(runbooks, alert.rule)
    if not ids:
        return ["no runbook answers this alert rule"]
    lines = []
    if len(ids) > MAX_RUNBOOKS:
        lines.append(f"{len(ids)} runbooks match; running the first {MAX_RUNBOOKS}")
    for rid in ids[:MAX_RUNBOOKS]:
        lines.append(report.summary(run_one(rid)))
    return lines


# --- Azure plumbing: the job's managed identity, Alerts Management and Queue Storage over REST -------------

def http(method: str, url: str, headers: dict, body: bytes | None = None, timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, method=method, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


_tokens: dict[str, tuple[str, float]] = {}


def msi_token(resource: str, env=os.environ, send=http, now=time.time) -> str:
    """A token from the Container Apps managed identity endpoint (user-assigned when AZURE_CLIENT_ID is set),
    kept until five minutes before it expires."""
    cached = _tokens.get(resource)
    if cached and cached[1] - 300 > now():
        return cached[0]
    query = {"resource": resource, "api-version": "2019-08-01"}
    if env.get("AZURE_CLIENT_ID"):
        query["client_id"] = env["AZURE_CLIENT_ID"]
    raw = json.loads(send("GET", f"{env['IDENTITY_ENDPOINT']}?{urllib.parse.urlencode(query)}",
                          {"X-IDENTITY-HEADER": env["IDENTITY_HEADER"]}))
    _tokens[resource] = (raw["access_token"], float(raw.get("expires_on") or 0))
    return raw["access_token"]


def arm_get_json(url: str, token=msi_token, send=http) -> dict:
    return json.loads(send("GET", url, {"Authorization": f"Bearer {token(ARM)}"}))


@dataclass
class Message:
    id: str
    pop_receipt: str
    dequeue_count: int
    text: str


class Queue:
    def __init__(self, account: str, name: str, token=msi_token, send=http):
        self.base = f"https://{account}.queue.core.windows.net/{name}/messages"
        self.token, self.send = token, send

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token(STORAGE)}", "x-ms-version": QUEUE_API}

    def receive(self, visibility_seconds: int) -> Message | None:
        raw = self.send("GET", f"{self.base}?numofmessages=1&visibilitytimeout={visibility_seconds}", self._headers())
        node = ET.fromstring(raw).find("QueueMessage")
        if node is None:
            return None
        return Message(node.findtext("MessageId", ""), node.findtext("PopReceipt", ""),
                       int(node.findtext("DequeueCount", "0") or 0), node.findtext("MessageText", ""))

    def delete(self, message: Message) -> None:
        receipt = urllib.parse.quote(message.pop_receipt, safe="")
        self.send("DELETE", f"{self.base}/{message.id}?popreceipt={receipt}", self._headers())


def main(env=os.environ, out=print) -> int:
    """The job's entry point: `python -m avd_runner alert-job`. One message per execution, so the flag is read
    for each message and one message's runbooks fit the replica timeout; the scaler starts the next execution
    while messages remain. Exit 0 when the message was handled, 1 when the flag or the queue failed."""
    import yaml

    from .cli import (ROOT, TIMEOUT_SECONDS, UNREADABLE, checked_finding, flag_position_with, flags,
                      post_runbook, unreachable)

    runbooks = load_all(ROOT)
    config = yaml.safe_load((ROOT / "governance/gateway.yml").read_text(encoding="utf-8"))
    queue = Queue(env["ALERT_QUEUE_ACCOUNT"], env["ALERT_QUEUE_NAME"])
    # Hidden for as long as the replica may run: a message can't be handed out twice while it is handled.
    visibility = MAX_RUNBOOKS * TIMEOUT_SECONDS + 300
    try:
        message = queue.receive(visibility)
    except Exception as error:
        out(f"error: the queue couldn't be read ({error_detail(error)})")
        return 1
    if message is None:
        out("no message")
        return 0
    code, detail = flag_position_with(arm_get_json, env["AZURE_SUBSCRIPTION_ID"], env["AVD_SWARM_RESOURCE_GROUP"],
                                      env.get("AVD_SWARM_SWITCH_TAG", "avd-swarm-switch"), list(flags.refusals(config)))
    if code == UNREADABLE and message.dequeue_count <= MAX_DEQUEUE:
        # Leave it hidden, not deleted: it comes back after the visibility timeout, at most MAX_DEQUEUE times,
        # so a broken flag read can't start an execution every polling interval.
        out(f"error: {detail}; the message waits ({message.dequeue_count} of {MAX_DEQUEUE} tries)")
        return 1

    def run_one(rid: str) -> dict:
        try:
            out(f"Asking the agent API to run {rid} (up to {TIMEOUT_SECONDS} seconds)")
            return checked_finding(post_runbook(env["AGENT_API_URL"], env["AGENT_API_RESOURCE"], rid, "alert",
                                                token=msi_token), rid)
        except Exception as error:
            return unreachable(rid, "alert", error)

    if message.dequeue_count > MAX_DEQUEUE:
        lines = [f"dropped a message seen {message.dequeue_count} times"]
    elif code != 0:
        lines = [f"dropped: {detail}"]
    else:
        lines = handle(message.text, arm_get_json, runbooks, run_one)
    for line in lines:
        out(line)
    try:
        queue.delete(message)
    except Exception as error:
        out(f"error: the message couldn't be deleted ({error_detail(error)}); it comes back after {visibility} seconds")
        return 1
    return 0


def error_detail(error: Exception) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTPError {error.code}"
    return type(error).__name__
