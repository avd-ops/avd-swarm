# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Unit tests for spikes/s1a/agent_api/openai_compat.py and Azure OpenAI meter pricing."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))
sys.path.insert(0, str(ROOT / "spikes/s1a/agent_api"))

import openai_compat  # noqa: E402
from avd_gateway.meter import Meter, SpendRefused  # noqa: E402
from avd_gateway.prices import PriceBook  # noqa: E402

BOOK = PriceBook.load(ROOT / "governance/model-prices.yml")


class OpenAICompatTests(unittest.TestCase):
    def test_to_openai_tools_and_tool_choice(self):
        tools = [
            {
                "name": "concierge_search",
                "description": "Search the KB",
                "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}},
            }
        ]
        converted = openai_compat.to_openai_tools(tools)
        self.assertEqual(len(converted), 1)
        self.assertEqual(converted[0]["type"], "function")
        self.assertEqual(converted[0]["function"]["name"], "concierge_search")
        self.assertEqual(
            openai_compat.to_openai_tool_choice({"type": "tool", "name": "report"}),
            {"type": "function", "function": {"name": "report"}},
        )
        self.assertEqual(openai_compat.to_openai_tool_choice({"type": "any"}), "required")
        self.assertEqual(openai_compat.to_openai_tool_choice({"type": "auto"}), "auto")

    def test_to_openai_messages_roundtrips_tool_use_and_tool_result(self):
        messages = [
            {"role": "user", "content": "Why did session host vm-0 drain fail?"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Checking Concierge and KQL."},
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "concierge_search",
                        "input": {"question": "drain fail", "top": 3},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_1",
                        "content": '{"hits": [{"id": "kb:hosts"}]}',
                    }
                ],
            },
        ]
        out = openai_compat.to_openai_messages("You are the Hosts specialist.", messages)
        self.assertEqual(out[0], {"role": "system", "content": "You are the Hosts specialist."})
        self.assertEqual(out[1], {"role": "user", "content": "Why did session host vm-0 drain fail?"})
        self.assertEqual(out[2]["role"], "assistant")
        self.assertEqual(out[2]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(out[2]["tool_calls"][0]["function"]["name"], "concierge_search")
        self.assertEqual(out[3], {"role": "tool", "tool_call_id": "call_1", "content": '{"hits": [{"id": "kb:hosts"}]}'})

    def test_from_openai_chat_completion_normalizes_tool_calls_and_cached_tokens(self):
        raw = {
            "model": "gpt-4.1-nano-2025-04-14",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": "Calling verify_citations.",
                        "tool_calls": [
                            {
                                "id": "call_verify_1",
                                "type": "function",
                                "function": {
                                    "name": "verify_citations",
                                    "arguments": '{"citations": ["kb:hosts#L1-L10"]}',
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 1200,
                "completion_tokens": 150,
                "prompt_tokens_details": {"cached_tokens": 400},
            },
        }
        msg = openai_compat.from_openai_chat_completion(raw, model_override="gpt-4.1-nano")
        self.assertEqual(msg.stop_reason, "tool_use")
        self.assertEqual(msg.model, "gpt-4.1-nano")
        self.assertEqual(msg.usage.input_tokens, 800)
        self.assertEqual(msg.usage.cache_read_input_tokens, 400)
        self.assertEqual(msg.usage.output_tokens, 150)
        self.assertEqual(len(msg.content), 2)
        self.assertEqual(msg.content[0].type, "text")
        self.assertEqual(msg.content[1].type, "tool_use")
        self.assertEqual(msg.content[1].name, "verify_citations")
        self.assertEqual(msg.content[1].input, {"citations": ["kb:hosts#L1-L10"]})

    def test_fallback_ladder_advances_on_transient_error_and_stops_on_spend_refused(self):
        candidates = openai_compat.candidate_models_for("gpt-4.1-nano")
        self.assertEqual(candidates, ["gpt-4.1-nano", "gpt-4.1-mini", "gpt-4o-mini"])

        calls = []

        async def flaky_invoke(model: str) -> str:
            calls.append(model)
            if model == "gpt-4.1-nano":
                raise RuntimeError("Azure OpenAI gpt-4.1-nano returned 429 TooManyRequests")
            return f"answered-by-{model}"

        result, used, attempts = asyncio.run(
            openai_compat.call_with_fallback_ladder(candidates, flaky_invoke, non_retriable=(SpendRefused,))
        )
        self.assertEqual(result, "answered-by-gpt-4.1-mini")
        self.assertEqual(used, "gpt-4.1-mini")
        self.assertEqual(calls, ["gpt-4.1-nano", "gpt-4.1-mini"])
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["status"], "error")
        self.assertEqual(attempts[1]["status"], "ok")

        async def refused_invoke(model: str) -> str:
            raise SpendRefused("run cap reached")

        with self.assertRaises(SpendRefused):
            asyncio.run(
                openai_compat.call_with_fallback_ladder(candidates, refused_invoke, non_retriable=(SpendRefused,))
            )

    def test_azure_openai_meter_books_sub_cent_nano_calls(self):
        m = Meter(cap_usd=1.0, prices=BOOK, provider="azure-openai")
        res = m.reserve("gpt-4.1-nano", input_tokens=5_000, max_tokens=1_000)
        cost = m.book(
            res,
            "gpt-4.1-nano",
            {"input_tokens": 4_000, "cache_read_input_tokens": 1_000, "output_tokens": 200},
        )
        # 4,000 * $0.10/1M + 1,000 * $0.025/1M + 200 * $0.40/1M = $0.0004 + $0.000025 + $0.00008 = $0.000505
        self.assertAlmostEqual(cost, 0.000505, places=7)

    def test_start_spike_bicep_parameters_match_main_bicep(self) -> None:
        import re

        bicep_text = (ROOT / "spikes/s1a/infra/main.bicep").read_text(encoding="utf-8")
        declared_params = set(re.findall(r"^\s*param\s+([A-Za-z0-9_]+)\s+", bicep_text, flags=re.MULTILINE))
        start_spike_text = (ROOT / "spikes/s1a/scripts/Start-Spike.ps1").read_text(encoding="utf-8")
        passed_params = set(re.findall(r'"([A-Za-z0-9_]+)=\$', start_spike_text))
        self.assertTrue(passed_params)
        unknown = passed_params - declared_params
        self.assertEqual(unknown, set(), f"Start-Spike.ps1 passes unrecognized Bicep parameters: {sorted(unknown)}")

    def test_spike_scripts_poll_long_operations_and_handle_cloud_shell_msi_tokens(self) -> None:
        spike_psm1 = (ROOT / "spikes/s1a/scripts/Spike.psm1").read_text(encoding="utf-8")
        start_spike = (ROOT / "spikes/s1a/scripts/Start-Spike.ps1").read_text(encoding="utf-8")
        remove_spike = (ROOT / "spikes/s1a/scripts/Remove-Spike.ps1").read_text(encoding="utf-8")
        invoke_spike = (ROOT / "spikes/s1a/Invoke-Spike.ps1").read_text(encoding="utf-8")

        self.assertIn("function Get-SpikeOperatorToken", spike_psm1)
        self.assertIn("not a supported MSI token audience", spike_psm1)
        self.assertIn("function Invoke-SpikeDeployment", spike_psm1)
        self.assertIn("function Remove-SpikeResourceGroup", spike_psm1)
        self.assertIn("Get-SpikeOperatorToken", start_spike)
        self.assertIn("Invoke-SpikeDeployment", start_spike)
        self.assertNotIn("az account get-access-token", start_spike)
        self.assertIn("Remove-SpikeResourceGroup", remove_spike)
        self.assertIn("cloudShellID", invoke_spike)
        self.assertIn("Get-SpikeOperatorToken", invoke_spike)
        self.assertNotIn("az account get-access-token", invoke_spike)


if __name__ == "__main__":
    unittest.main()

