# avd-swarm S1a spike: ask the hosted agent the eval questions. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Q2, Q4 and Q6 evidence: one request per question, with the answer, its usage and time.

Writes one JSON line per question to --out, then a token summary. Per-model token counts come
from Get-SpikeToken.ps1 (README block 7); this gives the per-question total.

    python run_evals.py --project-endpoint <url> --out ~/clouddrive/avdswarm-spike/evals.jsonl

The fallback without Foundry (agent_api/main.py in a Container App) instead:

    python run_evals.py --agent-url <url> --app-id <MCP app ID> --identity <agent identity app ID> \
        --model claude-sonnet-5-5 --out ~/clouddrive/avdswarm-spike/evals-claude-sonnet-5-5.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

QUESTIONS = Path(__file__).resolve().parent.parent / "evals" / "questions.json"
STOP_INPUT_TOKENS = 2_000_000  # docs/spikes/s1a.md section 3 stop rule
# Anthropic list prices per 1M tokens (input, output); cache reads bill at 0.1x input, 5-minute cache writes at 1.25x.
PRICES = {"claude-opus-5-5": (4.00, 20.00), "claude-sonnet-5-5": (2.00, 10.00), "claude-haiku-5-5": (0.10, 0.50)}


def cost_of(usage: dict) -> float:
    total = 0.0
    for model, u in usage.items():
        price_in, price_out = PRICES.get(model, (0.0, 0.0))
        total += (u["input"] + 0.1 * u["cacheRead"] + 1.25 * u["cacheWrite"]) * price_in / 1e6 + u["output"] * price_out / 1e6
    return round(total, 5)


def agent_api_answer(args, token: str, question: dict) -> dict:
    """One question to the fallback's agent service: start a job, then poll it (at most 10 minutes)."""
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=args.agent_url.rstrip("/"), headers=headers, timeout=60) as http:
        started = http.post("/ask", json={"question": question["text"], "model": args.model, "identity": args.identity})
        if started.status_code != 202:
            return {"error": {"step": "ask", "status": started.status_code, "body": started.text}}
        job_id = started.json()["id"]
        for _ in range(120):
            time.sleep(5)
            job = http.get(f"/ask/{job_id}")
            if job.status_code != 200:
                return {"error": {"step": "poll", "status": job.status_code, "body": job.text}}
            body = job.json()
            if body["status"] != "running":
                return body
    return {"error": {"step": "poll", "message": "no answer in 10 minutes"}}


def cli_token(scope: str) -> str:
    result = subprocess.run(["az", "account", "get-access-token", "--scope", scope, "--query", "accessToken", "-o", "tsv"],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.exit(f"az account get-access-token failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


def run_agent_api(args, questions: list, out: Path) -> None:
    token = cli_token(f"api://{args.app_id}/Mcp.Tools.ReadWrite")
    total_in = total_out = passed = 0
    total_cost = 0.0
    with out.open("a") as log:
        for question in questions:
            print(f"Asking {question['id']} (hosts on {args.model}): {question['text']}", flush=True)
            body = agent_api_answer(args, token, question)
            if body.get("status") != "done":
                record = {"id": question["id"], "model": args.model, "error": body.get("error", body)}
                log.write(json.dumps(record) + "\n")
                print(json.dumps(record))
                continue
            result = body["result"]
            answer = result["answer"] or ""
            missing = [p for p in question.get("expect", []) if not re.search(p, answer, re.IGNORECASE)]
            usage = result["usage"]
            record = {
                "id": question["id"], "kind": question["kind"], "agent": "agent-api", "model": args.model,
                "seconds": result["seconds"], "handedOff": result["handedOff"],
                "inputTokens": sum(u["input"] + u["cacheRead"] + u["cacheWrite"] for u in usage.values()),
                "outputTokens": sum(u["output"] for u in usage.values()),
                "costUsd": cost_of(usage), "usage": usage, "evidence": result["evidence"],
                "expectMet": not missing if "expect" in question else None, "expectMissing": missing, "answer": answer,
            }
            log.write(json.dumps(record) + "\n")
            check = "" if record["expectMet"] is None else (" PASS" if record["expectMet"] else f" CHECK (missing {missing})")
            print(f"  {record['seconds']}s, {record['inputTokens']} in / {record['outputTokens']} out, ${record['costUsd']}{check}\n  {answer[:400]}")
            passed += bool(record["expectMet"])
            total_in += record["inputTokens"]
            total_out += record["outputTokens"]
            total_cost += record["costUsd"]
            if total_in > STOP_INPUT_TOKENS:
                print(f"STOP: {total_in} input tokens is past the plan's stop rule; tear down (README block 9).")
                sys.exit(2)
    print(json.dumps({"step": "run-evals", "agent": "agent-api", "model": args.model, "questions": len(questions),
                      "expectMet": passed, "inputTokens": total_in, "outputTokens": total_out, "costUsd": round(total_cost, 4)}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-endpoint", help="Foundry: the project endpoint")
    parser.add_argument("--agent-url", help="Fallback: the agents Container App URL")
    parser.add_argument("--app-id", help="Fallback: the MCP server app's client ID (the agent service's audience)")
    parser.add_argument("--identity", help="Fallback: the agent identity's app ID")
    parser.add_argument("--model", help="Fallback: the Hosts agent's model")
    parser.add_argument("--agent-name", default="avdswarm-spike")
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", help="Comma-separated question IDs")
    args = parser.parse_args()

    questions = json.loads(QUESTIONS.read_text())
    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]

    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.agent_url:
        if not (args.app_id and args.identity and args.model):
            sys.exit("--agent-url needs --app-id, --identity and --model")
        run_agent_api(args, questions, out)
        return
    if not args.project_endpoint:
        sys.exit("Give --project-endpoint (Foundry) or --agent-url (fallback)")

    from azure.ai.projects import AIProjectClient  # Foundry only, so the fallback doesn't need it
    from azure.identity import AzureCliCredential
    from openai import APIStatusError

    project = AIProjectClient(endpoint=args.project_endpoint, credential=AzureCliCredential(), allow_preview=True)
    client = project.get_openai_client(agent_name=args.agent_name)

    total_in = total_out = passed = 0
    with out.open("a") as log:
        for question in questions:
            print(f"Asking {question['id']}: {question['text']}", flush=True)
            started = time.monotonic()
            try:
                response = client.responses.create(input=question["text"])
            except APIStatusError as error:
                record = {"id": question["id"], "error": {"status": error.status_code, "body": error.response.text,
                          "requestId": error.response.headers.get("x-request-id")}}
                log.write(json.dumps(record) + "\n")
                print(json.dumps(record))
                continue
            usage = response.usage
            answer = response.output_text or ""
            # A rough first check, not a grade: every expected pattern appears in the answer. Read the answers too.
            missing = [p for p in question.get("expect", []) if not re.search(p, answer, re.IGNORECASE)]
            record = {
                "id": question["id"], "kind": question["kind"], "agent": args.agent_name,
                "seconds": round(time.monotonic() - started, 1),
                "inputTokens": getattr(usage, "input_tokens", None), "outputTokens": getattr(usage, "output_tokens", None),
                # Everything the platform reports, including cached input tokens if it passes them through.
                "usage": usage.model_dump() if usage is not None else None,
                "expectMet": not missing if "expect" in question else None, "expectMissing": missing,
                "status": response.status, "answer": answer,
            }
            log.write(json.dumps(record) + "\n")
            check = "" if record["expectMet"] is None else (" PASS" if record["expectMet"] else f" CHECK (missing {missing})")
            print(f"  {record['seconds']}s, {record['inputTokens']} in / {record['outputTokens']} out{check}\n  {answer[:400]}")
            passed += bool(record["expectMet"])
            total_in += record["inputTokens"] or 0
            total_out += record["outputTokens"] or 0
            if total_in > STOP_INPUT_TOKENS:
                print(f"STOP: {total_in} input tokens is past the plan's stop rule; tear down (README block 9).")
                sys.exit(2)

    print(json.dumps({"step": "run-evals", "agent": args.agent_name, "questions": len(questions), "expectMet": passed,
                      "inputTokens": total_in, "outputTokens": total_out}))


if __name__ == "__main__":
    main()
