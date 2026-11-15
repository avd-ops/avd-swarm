# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Offline tests for Start-Spike.ps1 / Spike.psm1 policy-location exemption logic."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPIKE_PSM1 = ROOT / "spikes" / "s1a" / "scripts" / "Spike.psm1"
START_SPIKE_PS1 = ROOT / "spikes" / "s1a" / "scripts" / "Start-Spike.ps1"


def _find_powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


class SpikeLocationExemptionTests(unittest.TestCase):
    def test_start_spike_checks_allowed_locations_parameter_not_rg_equality(self):
        text = START_SPIKE_PS1.read_text(encoding="utf-8")
        self.assertIn("Test-SpikeLocationAllowed -Assignment $assignment -Location $Location", text)
        self.assertNotIn("$Location -eq $ResourceGroupLocation", text)

    def test_test_spike_location_allowed_evaluates_policy_parameter(self):
        shell = _find_powershell()
        if not shell:
            self.skipTest("PowerShell (pwsh/powershell) is not installed")

        script = f"""
$ErrorActionPreference = 'Stop'
Import-Module '{SPIKE_PSM1.as_posix()}' -Force
$centralOnly = [pscustomobject]@{{
    id = '/subscriptions/0000/providers/Microsoft.Authorization/policyAssignments/avdlz-allowed-locations'
    properties = [pscustomobject]@{{
        parameters = [pscustomobject]@{{
            listOfAllowedLocations = [pscustomobject]@{{ value = @('centralus') }}
        }}
    }}
}}
$includesEastUs2 = [pscustomobject]@{{
    properties = [pscustomobject]@{{
        parameters = [pscustomobject]@{{
            listOfAllowedLocations = [pscustomobject]@{{ value = @('centralus', 'East US 2') }}
        }}
    }}
}}
$missingParams = [pscustomobject]@{{ properties = [pscustomobject]@{{}} }}

[pscustomobject]@{{
    eastus2_on_central_only = Test-SpikeLocationAllowed -Assignment $centralOnly -Location 'eastus2'
    centralus_on_central_only = Test-SpikeLocationAllowed -Assignment $centralOnly -Location 'centralus'
    eastus2_on_display_name = Test-SpikeLocationAllowed -Assignment $includesEastUs2 -Location 'eastus2'
    missing_params = Test-SpikeLocationAllowed -Assignment $missingParams -Location 'eastus2'
}} | ConvertTo-Json -Compress
"""
        proc = subprocess.run(
            [shell, "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            check=True,
        )
        import json

        result = json.loads(proc.stdout.strip())
        self.assertFalse(result["eastus2_on_central_only"])
        self.assertTrue(result["centralus_on_central_only"])
        self.assertTrue(result["eastus2_on_display_name"])
        self.assertFalse(result["missing_params"])


if __name__ == "__main__":
    unittest.main()
