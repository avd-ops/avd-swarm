# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Tests for scripts/check_adapters.py (core-and-adapters.md section 3.6; decision 0009; red team P-5)."""

import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_adapters  # noqa: E402

GOOD = yaml.safe_load((ROOT / "adapters/apim/adapter.yml").read_text())


def errors_for(**changes):
    m = copy.deepcopy(GOOD)
    for key, value in changes.items():
        if value is None:
            m.pop(key)
        else:
            m[key] = value
    return check_adapters.manifest_errors("apim", m)


class Manifest(unittest.TestCase):
    def test_the_repo_passes(self):
        self.assertEqual(check_adapters.check(ROOT), [])

    def test_every_adapter_is_off(self):
        for name in ("sre-agent", "logic-apps", "apim", "foundry"):
            data = yaml.safe_load((ROOT / f"adapters/{name}/adapter.yml").read_text())
            self.assertIn(data["default"], (False, "off"), name)

    def assertFails(self, fragment, **changes):
        errors = errors_for(**changes)
        self.assertTrue(any(fragment in e for e in errors), errors)

    def test_on_by_default_fails(self):
        self.assertFails("default must be off", default="on")
        self.assertFails("default must be off", default=True)

    def test_replacing_a_core_capability_fails(self):
        self.assertFails("never replaces", replaces=["model-gateway"])

    def test_writing_fails(self):
        self.assertFails("writes must be none", writes="propose-and-apply")

    def test_an_exit_test_fails(self):
        self.assertFails("exitTests must be []", exitTests=["step-2-eval"])

    def test_identity_needs_the_policy_deny_and_watchdog(self):
        identity = dict(GOOD["identity"], policyDeny="")
        self.assertFails("identity.policyDeny", identity=identity)
        identity = dict(GOOD["identity"])
        identity.pop("watchdog")
        self.assertFails("identity.watchdog", identity=identity)
        self.assertFails("Owner and Contributor", identity=dict(GOOD["identity"], mustNotHold=["Reader"]))

    def test_every_switch_position_and_the_residual(self):
        switch = dict(GOOD["switch"])
        switch.pop("halt-agents")
        self.assertFails("switch.halt-agents", switch=switch)
        switch = dict(GOOD["switch"], residual=" ")
        self.assertFails("can't reach", switch=switch)

    def test_meters_need_a_source_and_say_if_standing(self):
        self.assertFails("meters must list", meters=[])
        meter = dict(GOOD["meters"][0], source="see the portal")
        self.assertFails("source must be an https link", meters=[meter])
        meter = dict(GOOD["meters"][0])
        meter.pop("standing")
        self.assertFails("standing", meters=[meter])

    def test_unknown_and_missing_keys(self):
        self.assertFails("unknown key 'apply'", apply="yes")
        self.assertFails("missing 'switch'", switch=None)

    def test_core_capability_and_kind_are_from_the_lists(self):
        self.assertFails("addsTo", addsTo="everything")
        self.assertFails("kind", kind="writer")


class Repo(unittest.TestCase):
    def copy_repo(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        for name in ("adapters", "gateway", "evals", ".github"):
            shutil.copytree(ROOT / name, tmp / name)
        return tmp

    def test_a_workflow_that_needs_an_adapter_fails(self):
        tmp = self.copy_repo()
        (tmp / ".github/workflows/x.yml").write_text("jobs: {a: {steps: [{run: cat adapters/sre-agent/adapter.yml}]}}\n")
        errors = check_adapters.check(tmp)
        self.assertTrue(any("depends on adapters/sre-agent" in e for e in errors), errors)

    def test_a_gateway_provider_without_its_adapter_fails(self):
        tmp = self.copy_repo()
        shutil.rmtree(tmp / "adapters/foundry")
        errors = check_adapters.check(tmp)
        self.assertTrue(any("needs adapter foundry" in e for e in errors), errors)

    def test_an_adapter_needs_its_page(self):
        tmp = self.copy_repo()
        (tmp / "adapters/apim/README.md").unlink()
        errors = check_adapters.check(tmp)
        self.assertTrue(any("no README.md" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
