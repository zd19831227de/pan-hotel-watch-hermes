from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class DualGatewayScriptTests(unittest.TestCase):
    def test_start_gateways_script_reads_profiles_from_manifest(self) -> None:
        text = (ROOT / "pan-hotel-hermes" / "scripts" / "start-hermes-gateways.ps1").read_text(encoding="utf-8")
        self.assertIn("[string]$ManifestPath", text)
        self.assertIn("feishu_profiles.py", text)
        self.assertIn('"validate"', text)
        self.assertIn('"list"', text)
        self.assertNotIn('@("manxin-ai", "jingli-ai")', text)
        self.assertIn("-p", text)
        self.assertIn("gateway", text)

    def test_secret_manifest_is_ignored_and_example_is_tracked(self) -> None:
        ignore_text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("pan-hotel-hermes/config/feishu-profiles.yaml", ignore_text)
        self.assertTrue(
            (ROOT / "pan-hotel-hermes" / "config" / "feishu-profiles.example.yaml").is_file()
        )

    def test_status_script_lists_gateway_profiles(self) -> None:
        text = (ROOT / "scripts" / "status.ps1").read_text(encoding="utf-8")
        self.assertIn("HermesGatewayProfiles", text)
        self.assertIn("gateway status", text)
        self.assertIn("FeishuProfilesManifest", text)
        self.assertIn("allowed_group_count", text)
        self.assertIn("Expected", text)
        self.assertIn("Running", text)
        self.assertIn("$decodedManifest = $manifestJson | ConvertFrom-Json", text)
        self.assertIn("$manifestProfileStatus = @($decodedManifest)", text)
        self.assertIn('"--context-api-base"', text)
        self.assertIn("$metadata.bound_stores -join", text)

    def test_stack_scripts_delegate_gateway_profiles_to_manifest_source(self) -> None:
        ps_text = (ROOT / "scripts" / "start-stack.ps1").read_text(encoding="utf-8")
        bat_text = (ROOT / "scripts" / "start-stack.bat").read_text(encoding="utf-8")
        self.assertIn("FeishuProfilesManifest", ps_text)
        self.assertIn("feishu_profiles.py", ps_text)
        self.assertNotIn('@("manxin-ai", "jingli-ai")', ps_text)
        self.assertIn("start-hermes-gateways.ps1", bat_text)
        self.assertNotIn("-p manxin-ai", bat_text)
        self.assertNotIn("-p jingli-ai", bat_text)

    def test_status_batch_delegates_to_manifest_aware_powershell_status(self) -> None:
        text = (ROOT / "scripts" / "status.bat").read_text(encoding="utf-8")
        self.assertIn("status.ps1", text)


if __name__ == "__main__":
    unittest.main()
