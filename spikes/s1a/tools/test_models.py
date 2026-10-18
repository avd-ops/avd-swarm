# avd-swarm S1a spike: Q1, call each Claude deployment once. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Q1 evidence: each deployment answers a test prompt through Foundry with an Entra token (no key).

    python test_models.py --resource <foundry account name> --deployment claude-opus-5-5 --deployment claude-haiku-5-5
"""

from __future__ import annotations

import argparse
import json
import sys

from anthropic import APIStatusError, AnthropicFoundry
from azure.identity import AzureCliCredential, get_bearer_token_provider


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resource", required=True)
    parser.add_argument("--deployment", action="append", required=True)
    args = parser.parse_args()

    token_provider = get_bearer_token_provider(AzureCliCredential(), "https://ai.azure.com/.default")
    client = AnthropicFoundry(resource=args.resource, azure_ad_token_provider=token_provider)
    failed = False
    for deployment in args.deployment:
        print(f"Calling {deployment}...", flush=True)
        try:
            message = client.messages.create(
                model=deployment,
                max_tokens=256,
                messages=[{"role": "user", "content": "Reply with one short sentence: which model are you?"}],
            )
        except APIStatusError as error:
            failed = True
            print(json.dumps({"step": "test-model", "deployment": deployment, "status": error.status_code,
                              "body": error.response.text, "requestId": error.response.headers.get("request-id")}))
            continue
        text = "".join(block.text for block in message.content if block.type == "text")
        print(json.dumps({"step": "test-model", "deployment": deployment, "stopReason": message.stop_reason,
                          "inputTokens": message.usage.input_tokens, "outputTokens": message.usage.output_tokens,
                          "text": text}))
    print("RESULT Q1 FAIL" if failed else "RESULT Q1 PASS: every deployment answered with an Entra token")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
