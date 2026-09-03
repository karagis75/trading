"""Linux VPS systemd units stay aligned with Compose and the daily runner."""

from __future__ import annotations

import stat
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
LINUX_DIR = REPO_ROOT / "deploy" / "linux"


class LinuxVpsUnitTests(unittest.TestCase):
    def test_units_are_templates_for_the_clone_path(self) -> None:
        web = (LINUX_DIR / "trading-web.service").read_text(encoding="utf-8")
        daily = (LINUX_DIR / "trading-daily.service").read_text(encoding="utf-8")
        timer = (LINUX_DIR / "trading-daily.timer").read_text(encoding="utf-8")
        self.assertIn("WorkingDirectory=__TRADING_HOME__", web)
        self.assertIn("WorkingDirectory=__TRADING_HOME__", daily)
        self.assertIn("docker compose up -d", web)
        self.assertIn("docker compose run --rm daily", daily)
        self.assertIn("TimeoutStartSec=21600", daily)
        self.assertIn("OnCalendar=*-*-* 08:00:00", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("Asia/Kolkata", timer)
        self.assertNotIn("PASSWORD=", web + daily + timer)

    def test_install_script_is_executable_and_dry_writable(self) -> None:
        script = LINUX_DIR / "install-vps.sh"
        self.assertTrue(script.is_file())
        self.assertTrue(
            script.stat().st_mode & stat.S_IXUSR,
            "install-vps.sh must be executable",
        )


class LinuxVpsInstallScriptTests(unittest.TestCase):
    def test_install_writes_substituted_units_without_systemctl(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            unit_dir = Path(temp_dir) / "units"
            env_path = REPO_ROOT / ".env"
            created_env = False
            if not env_path.exists():
                env_path.write_text(
                    "POSTGRES_USER=trading_app\nPOSTGRES_PASSWORD=test\nPOSTGRES_DB=trading_history\n",
                    encoding="utf-8",
                )
                created_env = True
            try:
                proc = subprocess.run(
                    [
                        str(LINUX_DIR / "install-vps.sh"),
                        "--home",
                        str(REPO_ROOT),
                        "--unit-dir",
                        str(unit_dir),
                        "--no-enable",
                    ],
                    cwd=REPO_ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                web = (unit_dir / "trading-web.service").read_text(encoding="utf-8")
                daily = (unit_dir / "trading-daily.service").read_text(encoding="utf-8")
                self.assertIn(f"WorkingDirectory={REPO_ROOT}", web)
                self.assertNotIn("__TRADING_HOME__", web)
                self.assertIn("docker compose run --rm daily", daily)
                self.assertTrue((unit_dir / "trading-daily.timer").is_file())
            finally:
                if created_env:
                    env_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
