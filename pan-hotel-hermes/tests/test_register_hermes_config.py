from __future__ import annotations

import unittest
from pathlib import Path

REGISTER_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "register-hermes.ps1"


class RegisterHermesCompressionConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script_text = REGISTER_SCRIPT.read_text(encoding="utf-8")

    def test_register_script_includes_compression_block(self) -> None:
        self.assertIn("compression:", self.script_text)
        self.assertIn("enabled: true", self.script_text)
        self.assertIn("threshold: 0.45", self.script_text)
        self.assertIn("protect_last_n: 24", self.script_text)

    def test_register_script_merges_compression_when_missing(self) -> None:
        self.assertIn("compression:", self.script_text)
        self.assertIn("protect_last_n", self.script_text)

    def test_register_script_hides_tool_progress_in_gateway_replies(self) -> None:
        self.assertIn("tool_progress: off", self.script_text)
        self.assertIn("display.tool_progress", self.script_text)

    def test_register_script_documents_gateway_restart(self) -> None:
        self.assertIn("RestartGateway", self.script_text)
        self.assertIn("gateway stop", self.script_text)
        self.assertIn("gateway run", self.script_text)

    def test_register_script_has_restart_gateway_switch(self) -> None:
        self.assertIn("[switch]$RestartGateway", self.script_text)

    def test_register_script_checks_python_runtime_before_mcp_registration(self) -> None:
        self.assertIn("Test-PanHotelHermesPython", self.script_text)
        self.assertIn("import encodings, mcp", self.script_text)
        self.assertIn("Get-Command python", self.script_text)

    def test_register_script_syncs_existing_mcp_python_command(self) -> None:
        self.assertIn("$McpPythonCommand", self.script_text)
        self.assertIn("pan-hotel-watch MCP command", self.script_text)

    def test_register_script_supports_named_profile_root(self) -> None:
        self.assertIn("[string]$Profile", self.script_text)
        self.assertIn("profiles", self.script_text)
        self.assertIn("HERMES_HOME", self.script_text)

    def test_register_script_composes_base_soul_with_profile_overlay(self) -> None:
        self.assertIn("$BaseSoulSrc", self.script_text)
        self.assertIn("$ProfileSoulSrc", self.script_text)
        self.assertIn("profile-specific SOUL", self.script_text)
        # SOUL is now written via the UTF-8-no-BOM helper instead of Set-Content.
        self.assertIn("Write-Utf8NoBomFile", self.script_text)
        self.assertIn("$SoulDst", self.script_text)

    def test_register_script_can_reset_profile_memory_templates(self) -> None:
        self.assertIn("ResetMemory", self.script_text)
        self.assertIn("hermes.MEMORY.template.md", self.script_text)
        self.assertIn("hermes.USER.template.md", self.script_text)

    def test_register_script_extends_browser_run_timeout_for_slow_reads(self) -> None:
        self.assertIn("$BrowserRunTimeoutSeconds", self.script_text)
        self.assertIn('Get-PanHotelWatchEnvValue -Name "WATCH_API_BROWSER_TIMEOUT" -Default "300"', self.script_text)
        self.assertIn('WATCH_API_BROWSER_TIMEOUT: "$BrowserRunTimeoutSeconds"', self.script_text)
        self.assertIn("timeout: $BrowserRunTimeoutSeconds", self.script_text)


if __name__ == "__main__":
    unittest.main()
