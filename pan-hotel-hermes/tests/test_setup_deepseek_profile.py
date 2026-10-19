from __future__ import annotations

import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "setup-deepseek.ps1"


class SetupDeepseekProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script_text = SCRIPT.read_text(encoding="utf-8")

    def test_setup_script_targets_a_named_hermes_profile(self) -> None:
        self.assertIn('[string]$Profile = "default"', self.script_text)
        self.assertIn("$env:HERMES_HOME = $HermesRoot", self.script_text)
        self.assertIn('Join-Path (Join-Path $base "profiles") $name', self.script_text)

    def test_setup_script_registers_and_can_restart_the_target_profile(self) -> None:
        self.assertIn("[switch]$RestartGateway", self.script_text)
        self.assertIn("-Profile $Profile", self.script_text)
        self.assertIn("-RestartGateway:$RestartGateway", self.script_text)

    def test_setup_script_fails_when_hermes_config_write_fails(self) -> None:
        self.assertIn("$LASTEXITCODE", self.script_text)
        self.assertIn("Failed to set DEEPSEEK_API_KEY", self.script_text)

    def test_setup_script_is_ascii_for_windows_powershell_5(self) -> None:
        self.assertTrue(self.script_text.isascii())


if __name__ == "__main__":
    unittest.main()
