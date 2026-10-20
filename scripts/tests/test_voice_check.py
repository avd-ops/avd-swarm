# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""avd-buddy's voice rules hold what docs/buddy-voice.md says, on every fixed eval case."""

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import voice_check as vc  # noqa: E402

SETTINGS = yaml.safe_load((ROOT / "governance/buddy-voice.yml").read_text(encoding="utf-8"))
CASES = yaml.safe_load((ROOT / "evals/voice/cases.yml").read_text(encoding="utf-8"))["cases"]


def turn_for(case: dict, **overrides) -> vc.Turn:
    fields = {
        "events": set(case.get("events", [])),
        "operator_text": case["operator_text"],
        "language": case.get("language", "en"),
        "humor_setting": case.get("humor_setting", "light"),
    }
    fields.update(overrides)
    return vc.Turn(**fields)


class EvalCases(unittest.TestCase):
    def test_case_ids_unique(self):
        ids = [c["id"] for c in CASES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_mode_and_aside_match_every_case(self):
        for case in CASES:
            with self.subTest(case=case["id"]):
                turn = turn_for(case)
                self.assertEqual(vc.get_mode(turn, SETTINGS), case["mode"])
                self.assertEqual(vc.aside_allowed(turn, SETTINGS), case["aside"] == "allowed")

    def test_every_mode_is_covered(self):
        self.assertEqual({c["mode"] for c in CASES}, {vc.LIGHT, vc.SOFT_STEADY, vc.HARD_STEADY})


class Modes(unittest.TestCase):
    def test_hard_steady_beats_an_invitation(self):
        turn = vc.Turn(events={"approval-pending", "run-failed"}, operator_text="haha cheer me up")
        self.assertEqual(vc.get_mode(turn, SETTINGS), vc.HARD_STEADY)
        self.assertFalse(vc.aside_allowed(turn, SETTINGS))

    def test_model_can_ask_for_steady_never_light(self):
        turn = vc.Turn(model_asked_steady=True)
        self.assertEqual(vc.get_mode(turn, SETTINGS), vc.SOFT_STEADY)
        self.assertFalse(vc.aside_allowed(turn, SETTINGS))

    def test_stress_words_are_whole_words(self):
        self.assertEqual(vc.get_mode(vc.Turn(operator_text="Show the download size"), SETTINGS), vc.LIGHT)
        self.assertEqual(vc.get_mode(vc.Turn(operator_text="pool B is down"), SETTINGS), vc.SOFT_STEADY)

    def test_aside_frequency_cap(self):
        self.assertFalse(vc.aside_allowed(vc.Turn(replies_since_aside=1), SETTINGS))
        self.assertTrue(vc.aside_allowed(vc.Turn(replies_since_aside=2), SETTINGS))

    def test_unknown_event_is_not_light_by_accident(self):
        # A new event kind must be added to the settings to change the mode; until then it changes nothing.
        self.assertEqual(vc.get_mode(vc.Turn(events={"something-new"}), SETTINGS), vc.LIGHT)

    def test_every_event_is_in_one_list_only(self):
        hard, soft = set(SETTINGS["hard_steady_events"]), set(SETTINGS["soft_steady_events"])
        self.assertFalse(hard & soft)


class Asides(unittest.TestCase):
    GOOD = [
        "Idle hosts: excellent at relaxing, less good at earning their keep.",
        "Your profiles are roaming more than a cat at night.",
        "Somewhere a session host is enjoying its drain mode like a long weekend.",
    ]

    def test_good_asides_pass(self):
        for aside in self.GOOD:
            with self.subTest(aside=aside):
                self.assertEqual(vc.get_aside_findings(aside, SETTINGS), [])

    def test_banned_topics_fail(self):
        cases = {
            "This config is more divided than an election.": "banned-politics",
            "Pray the image build finishes.": "banned-religion",
            "That VM size is looking sexy.": "banned-sex",
            "Your boss would love this scaling plan.": "banned-people_and_employers",
            "At least it was not a ransomware day.": "banned-incidents",
            "Time for a beer while it deploys.": "banned-substances",
        }
        for aside, finding in cases.items():
            with self.subTest(aside=aside):
                self.assertIn(finding, vc.get_aside_findings(aside, SETTINGS))

    def test_an_aside_never_carries_a_fact(self):
        cases = {
            "Only 3 hosts left awake.": "carries-digits",
            "More at https://example.com tonight.": "carries-link",
            "Even `hp-01` needs a nap.": "carries-code",
            "Nice work :)": "carries-emoticon",
        }
        for aside, finding in cases.items():
            with self.subTest(aside=aside):
                self.assertIn(finding, vc.get_aside_findings(aside, SETTINGS))

    def test_no_emoji_and_one_sentence(self):
        self.assertIn("emoji-or-symbol", vc.get_aside_findings("Deployed \U0001F680", SETTINGS))
        self.assertIn("more-than-one-sentence", vc.get_aside_findings("One. Two.", SETTINGS))
        self.assertIn("too-long", vc.get_aside_findings("word " * 40, SETTINGS))

    def test_curly_punctuation_is_not_an_emoji(self):
        self.assertEqual(vc.get_aside_findings("It’s fine — the hosts are napping.", SETTINGS), [])

    def test_estate_names_fail(self):
        findings = vc.get_aside_findings("Pool Contoso-Sales is napping.", SETTINGS, ["Contoso-Sales"])
        self.assertIn("carries-estate-name", findings)

    def test_check_reply_flags_an_aside_in_steady_mode(self):
        turn = vc.Turn(events={"run-failed"}, operator_text="it failed")
        self.assertEqual(vc.check_reply(turn, self.GOOD[0], SETTINGS), ["aside-in-soft-steady"])
        self.assertEqual(vc.check_reply(turn, None, SETTINGS), [])


if __name__ == "__main__":
    unittest.main()
