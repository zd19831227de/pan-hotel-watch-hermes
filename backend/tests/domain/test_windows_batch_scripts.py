from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.workers import read_only_scheduler


ROOT = Path(__file__).resolve().parents[3]


class WindowsBatchScriptTests(unittest.TestCase):
    def test_install_and_start_entries_are_batch_files_without_powershell_dependency(self) -> None:
        # Pure batch entries must not depend on PowerShell, so the core stack
        # (install/run-*/start-all) stays single-runtime. start-stack.bat is
        # allowed to delegate Hermes gateway start to start-hermes-gateways.ps1
        # (Step 3A: gateway profiles come from the yaml manifest); it has its
        # own dedicated tests below.
        pure_batch_names = (
            "install.bat",
            "start-all.bat",
            "run-backend.bat",
            "run-worker.bat",
            "run-frontend.bat",
            "register-startup-task.bat",
            "unregister-startup-task.bat",
        )
        for name in pure_batch_names:
            path = ROOT / "scripts" / name
            self.assertTrue(path.exists(), name)
            source = path.read_text(encoding="utf-8-sig").lower()
            self.assertNotIn("powershell", source, name)
            self.assertNotIn(".ps1", source, name)

    def test_start_stack_delegates_gateway_to_hermes_ps1_from_manifest(self) -> None:
        # start-stack.bat may invoke PowerShell only to launch Hermes gateway
        # profiles declared in the feishu-profiles.yaml manifest (Step 3A).
        source = (ROOT / "scripts" / "start-stack.bat").read_text(
            encoding="utf-8-sig"
        ).lower()
        self.assertIn("start-hermes-gateways.ps1", source)
        self.assertIn("feishu-profiles.yaml", source)

    def test_batch_stack_uses_consistent_backend_port_and_proxy(self) -> None:
        source = "\n".join(
            (
                (ROOT / "scripts" / "common.bat").read_text(encoding="utf-8-sig"),
                (ROOT / "scripts" / "start-stack.bat").read_text(encoding="utf-8-sig"),
            )
        )
        self.assertIn("8100", source)
        self.assertIn("VITE_API_PROXY_TARGET", source)

    def test_batch_stack_fails_closed_when_backend_is_not_healthy(self) -> None:
        source = (ROOT / "scripts" / "start-stack.bat").read_text(encoding="utf-8-sig")
        normalized = source.replace("\r\n", "\n")
        self.assertIn("echo ERROR: backend did not become healthy.", normalized)
        self.assertIn(
            "echo ERROR: backend did not become healthy. Check .runtime\\logs\\backend.err.log\nexit /b 1",
            normalized,
        )

    def test_batch_stack_uses_direct_background_processes(self) -> None:
        source = (ROOT / "scripts" / "start-stack.bat").read_text(encoding="utf-8-sig").lower()
        self.assertNotIn('start "pan hotel watch backend" /b cmd /d /c', source)
        self.assertNotIn('start "pan hotel watch worker" /b cmd /d /c', source)
        self.assertNotIn('start "pan hotel watch frontend" /b cmd /d /c', source)

    def test_batch_stack_does_not_start_a_second_worker_when_pid_is_alive(self) -> None:
        source = (ROOT / "scripts" / "start-stack.bat").read_text(encoding="utf-8-sig").lower()
        self.assertIn("worker_pid_file", source)
        self.assertIn("worker_already_running", source)
        self.assertIn("tasklist", source)

    def test_stop_batch_uses_worker_pid_file_before_legacy_wmic_fallback(self) -> None:
        source = (ROOT / "scripts" / "stop-all.bat").read_text(encoding="utf-8-sig").lower()
        self.assertIn("worker_pid_file", source)
        self.assertIn("taskkill /pid", source)
        self.assertIn("app.workers.read_only_scheduler", source)

    def test_worker_records_and_cleans_its_runtime_pid(self) -> None:
        source = (ROOT / "backend" / "app" / "workers" / "read_only_scheduler.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("read-only-worker.pid", source)
        self.assertIn("os.getpid()", source)
        self.assertIn("finally", source)

    def test_worker_pid_cleanup_does_not_delete_a_replacement_worker_pid(self) -> None:
        with TemporaryDirectory() as temp_dir, patch.object(read_only_scheduler.os, "getpid", return_value=1234):
            pid_path = Path(temp_dir) / "read-only-worker.pid"
            read_only_scheduler._write_worker_pid(pid_path)
            read_only_scheduler._remove_worker_pid(pid_path)
            self.assertFalse(pid_path.exists())

            pid_path.write_text("5678", encoding="ascii")
            read_only_scheduler._remove_worker_pid(pid_path)
            self.assertTrue(pid_path.exists())

    def test_status_script_resolves_batch_started_processes_without_ps1_pid_files(self) -> None:
        source = (ROOT / "scripts" / "status.ps1").read_text(encoding="utf-8-sig").lower()

        self.assertIn("resolve-panhotelwatchstatusprocess", source)
        self.assertIn("portowner.processid", source)
        self.assertIn('"read-only-worker.pid"', source)


if __name__ == "__main__":
    unittest.main()
