#!/usr/bin/env python3
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Runs inside a short-lived ACI with UAMIs assigned to verify §6 app-only IMDS token assumptions.

Tests:
- Assumptions 1 & 2: UAMI with no Graph app role, holding Directory Readers + AU-scoped custom role
  (microsoft.directory/groups/members/update), calling POST/DELETE /groups/{id}/members/$ref.
- Assumption 3: UAMI with AU-scoped User Administrator calling POST /users/{id}/revokeSignInSessions.
- Assumption 5: UAMI with custom AVD host-drain role calling PATCH/PUT sessionHosts/write with only allowNewSession.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

IMDS_URL = "http://169.254.169.254/metadata/identity/oauth2/token"


def get_imds_token(resource: str, client_id: str) -> str:
    params = urllib.parse.urlencode(
        {"api-version": "2018-02-01", "resource": resource, "client_id": client_id}
    )
    req = urllib.request.Request(f"{IMDS_URL}?{params}", headers={"Metadata": "true"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))["access_token"]


def http_call(method: str, url: str, token: str, body: dict | None = None) -> tuple[int, str]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def main() -> int:
    results: dict[str, dict] = {}
    quarantine_cid = os.environ["QUARANTINE_CLIENT_ID"]
    revoke_cid = os.environ["REVOKE_CLIENT_ID"]
    drain_cid = os.environ["DRAIN_CLIENT_ID"]
    in_au_group = os.environ["IN_AU_GROUP_ID"]
    outside_au_group = os.environ["OUTSIDE_AU_GROUP_ID"]
    test_user = os.environ["TEST_USER_ID"]
    outside_user = os.environ.get("OUTSIDE_AU_USER_ID", "")
    host_pool_id = os.environ.get("HOST_POOL_RESOURCE_ID", "")
    session_host = os.environ.get("SESSION_HOST_NAME", "")

    # 1 & 2: Graph group member add/remove with AU-scoped custom role + Directory Readers (no Graph app role)
    q_token = get_imds_token("https://graph.microsoft.com/", quarantine_cid)
    add_status, add_body = http_call(
        "POST",
        f"https://graph.microsoft.com/v1.0/groups/{in_au_group}/members/$ref",
        q_token,
        {"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{test_user}"},
    )
    del_status, del_body = http_call(
        "DELETE",
        f"https://graph.microsoft.com/v1.0/groups/{in_au_group}/members/{test_user}/$ref",
        q_token,
    )
    neg_status, neg_body = http_call(
        "POST",
        f"https://graph.microsoft.com/v1.0/groups/{outside_au_group}/members/$ref",
        q_token,
        {"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{test_user}"},
    )
    results["assumptions_1_2_quarantine_au"] = {
        "addInAuStatus": add_status,
        "removeInAuStatus": del_status,
        "negativeOutsideAuStatus": neg_status,
        "passed": add_status in (200, 204) and del_status in (200, 204) and neg_status == 403,
        "addError": add_body[:300] if add_status not in (200, 204) else "",
        "removeError": del_body[:300] if del_status not in (200, 204) else "",
        "negativeNote": neg_body[:200] if neg_status != 403 else "",
    }

    # 3: revokeSignInSessions with AU-scoped User Administrator
    r_token = get_imds_token("https://graph.microsoft.com/", revoke_cid)
    rev_status, rev_body = http_call(
        "POST",
        f"https://graph.microsoft.com/v1.0/users/{test_user}/revokeSignInSessions",
        r_token,
        {},
    )
    neg_rev_status = None
    if outside_user:
        neg_rev_status, _ = http_call(
            "POST",
            f"https://graph.microsoft.com/v1.0/users/{outside_user}/revokeSignInSessions",
            r_token,
            {},
        )
    results["assumption_3_revoke_signin_sessions"] = {
        "revokeInAuStatus": rev_status,
        "negativeOutsideAuStatus": neg_rev_status,
        "passed": rev_status == 200 and (neg_rev_status in (None, 403)),
        "responseSnippet": rev_body[:300],
    }

    # 5: sessionHosts/write with body containing only allowNewSession
    if host_pool_id and session_host:
        arm_token = get_imds_token("https://management.azure.com/", drain_cid)
        sh_url = (
            f"https://management.azure.com{host_pool_id}/sessionHosts/{session_host}"
            "?api-version=2024-04-03"
        )
        get_status, get_body = http_call("GET", sh_url, arm_token)
        orig_allow = None
        if get_status == 200:
            orig_allow = json.loads(get_body).get("properties", {}).get("allowNewSession")
        patch_status, patch_body = http_call(
            "PATCH", sh_url, arm_token, {"properties": {"allowNewSession": False}}
        )
        # Restore original drain state immediately
        restore_status = None
        if orig_allow is not None and patch_status in (200, 201):
            restore_status, _ = http_call(
                "PATCH", sh_url, arm_token, {"properties": {"allowNewSession": orig_allow}}
            )
        results["assumption_5_sessionhost_write_allow_new_session"] = {
            "getStatus": get_status,
            "patchStatus": patch_status,
            "restoreStatus": restore_status,
            "passed": patch_status in (200, 201),
            "patchSnippet": patch_body[:300] if patch_status not in (200, 201) else "",
        }
    else:
        results["assumption_5_sessionhost_write_allow_new_session"] = {
            "skipped": True,
            "reason": "No HOST_POOL_RESOURCE_ID / SESSION_HOST_NAME provided.",
        }

    print("BOUNCER_VERIFY_RESULTS=" + json.dumps(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
