# avd-swarm S1a spike: ask the hosted agent the eval questions. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Q2, Q4 and Q6 evidence: one request per question, with the answer, its usage and time.

Writes one JSON line per question to --out, then a token summary. Per-model token counts come
from Get-SpikeToken.ps1 (README block 7); this gives the per-question total.

    python run_evals.py --project-endpoint <url> --out ~/clouddrive/avdswarm-spike/evals.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential
from openai import APIStatusError

QUESTIONS = Path(__file__).resolve().parent.parent / "evals" / "questions.json"
STOP_INPUT_TOKENS = 2_000_000  # docs/spikes/s1a.md section 3 stop rule


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--agent-name", default="avdswarm-spike")
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", help="Comma-separated question IDs")
    args = parser.parse_args()

    questions = json.loads(QUESTIONS.read_text())
    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]

    project = AIProjectClient(endpoint=args.project_endpoint, credential=AzureCliCredential(), allow_preview=True)
    client = project.get_openai_client(agent_name=args.agent_name)
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)

    total_in = total_out = 0
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
            record = {
                "id": question["id"], "kind": question["kind"], "seconds": round(time.monotonic() - started, 1),
                "inputTokens": getattr(usage, "input_tokens", None), "outputTokens": getattr(usage, "output_tokens", None),
                "status": response.status, "answer": response.output_text,
            }
            log.write(json.dumps(record) + "\n")
            print(f"  {record['seconds']}s, {record['inputTokens']} in / {record['outputTokens']} out\n  {record['answer'][:400]}")
            total_in += record["inputTokens"] or 0
            total_out += record["outputTokens"] or 0
            if total_in > STOP_INPUT_TOKENS:
                print(f"STOP: {total_in} input tokens is past the plan's stop rule; tear down (README block 9).")
                sys.exit(2)

    print(json.dumps({"step": "run-evals", "questions": len(questions), "inputTokens": total_in, "outputTokens": total_out}))


if __name__ == "__main__":
    main()
