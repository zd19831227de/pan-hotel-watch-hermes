from __future__ import annotations

import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "setup-feishu-gateway.ps1"


class SetupFeishuGatewayProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script_text = SCRIPT.read_text(encoding="utf-8")

    def test_setup_script_accepts_profile_and_allowed_chats(self) -> None:
        self.assertIn("[string]$Profile", self.script_text)
        self.assertIn("[string]$AllowedChats", self.script_text)
        self.assertIn("FEISHU_ALLOWED_CHATS", self.script_text)
        self.assertIn("FEISHU_HOME_CHANNEL", self.script_text)
        self.assertIn("Resolve-HomeChannelFromAllowedChats", self.script_text)

    def test_setup_script_accepts_explicit_app_credentials_without_project_env(self) -> None:
        self.assertIn("[string]$AppId", self.script_text)
        self.assertIn("[securestring]$AppSecret", self.script_text)
        self.assertIn("FEISHU_APP_SECRET", self.script_text)

    def test_setup_script_prefers_existing_profile_credentials_before_project_env(self) -> None:
        self.assertIn('Read-DotEnvValue -Path $HermesEnv -Key "FEISHU_APP_ID"', self.script_text)
        self.assertIn('Read-DotEnvValue -Path $HermesEnv -Key "FEISHU_APP_SECRET"', self.script_text)

    def test_setup_script_uses_manifest_for_profile_and_backend_configuration(self) -> None:
        self.assertIn("[string]$ManifestPath", self.script_text)
        self.assertIn("[string]$BackendEnvPath", self.script_text)
        self.assertIn("feishu_profiles.py", self.script_text)
        self.assertIn(" apply --profile ", self.script_text)
        self.assertIn('"write-backend-env" --dotenv $BackendEnvPath', self.script_text)
        self.assertNotIn("app_secret", "\n".join(
            line for line in self.script_text.splitlines() if "Write-Host" in line
        ).lower())


if __name__ == "__main__":
    unittest.main()
