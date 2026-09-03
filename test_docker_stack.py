"""Checks that Docker/Compose stay aligned with the host bind and daily runner."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import daily_once_runner as runner
from webapp.config import AppConfig

REPO_ROOT = Path(__file__).resolve().parent
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
DOCKERFILE_PATH = REPO_ROOT / "Dockerfile"
REQUIREMENTS_PATH = REPO_ROOT / "requirements.txt"


class RequirementsTests(unittest.TestCase):
    def test_runtime_deps_are_unpinned_and_inferred(self) -> None:
        lines = [
            line.strip()
            for line in REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        self.assertEqual(
            lines,
            [
                "psycopg[binary]",
                "flask",
                "pandas",
                "numpy",
                "yfinance",
                "openpyxl",
                "requests",
                "curl_cffi",
            ],
        )
        for line in lines:
            self.assertNotIn("==", line)
            self.assertNotIn(">=", line)


class DockerfileTests(unittest.TestCase):
    def test_image_binds_all_interfaces_and_uses_kolkata_tz(self) -> None:
        text = DOCKERFILE_PATH.read_text(encoding="utf-8")
        self.assertIn("TRADING_WEB_HOST=0.0.0.0", text)
        self.assertIn("TRADING_WEB_PORT=8000", text)
        self.assertIn("TZ=Asia/Kolkata", text)
        self.assertIn('CMD ["python", "-m", "webapp"]', text)
        self.assertIn("EXPOSE 8000", text)
        self.assertNotIn("TRADING_DATABASE_URL=", text)
        self.assertNotRegex(text, r"PASSWORD\s*=")


class ComposeConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = COMPOSE_PATH.read_text(encoding="utf-8")

    def test_core_services_and_daily_profile(self) -> None:
        self.assertRegex(self.text, r"(?m)^  postgres:\s*$")
        self.assertRegex(self.text, r"(?m)^  web:\s*$")
        self.assertRegex(self.text, r"(?m)^  daily:\s*$")
        self.assertIn('profiles: ["daily"]', self.text)
        self.assertIn("daily_once_runner.py", self.text)
        self.assertIn("./scheduler/state:/app/scheduler/state", self.text)

    def test_web_publishes_8000_on_all_interfaces(self) -> None:
        self.assertIn('TRADING_WEB_HOST: "0.0.0.0"', self.text)
        self.assertIn('"8000:8000"', self.text)
        self.assertIn("TZ: Asia/Kolkata", self.text)

    def test_database_url_points_at_compose_postgres(self) -> None:
        self.assertIn("TRADING_DATABASE_URL:", self.text)
        self.assertIn(
            "postgresql://${POSTGRES_USER:-trading_app}:${POSTGRES_PASSWORD:-trading}"
            "@postgres:5432/${POSTGRES_DB:-trading_history}",
            self.text,
        )


class WebBindTests(unittest.TestCase):
    def test_host_default_stays_loopback(self) -> None:
        cfg = AppConfig()
        self.assertEqual(cfg.host, "127.0.0.1")
        self.assertEqual(cfg.port, 8000)

    def test_container_host_comes_from_env(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"TRADING_WEB_HOST": "0.0.0.0", "TRADING_WEB_PORT": "8000"},
            clear=False,
        ):
            cfg = AppConfig.from_env()
        self.assertEqual(cfg.host, "0.0.0.0")
        self.assertEqual(cfg.port, 8000)


class DailyRunnerCliTests(unittest.TestCase):
    def test_help_describes_once_per_day_gate(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "daily_once_runner.py"), "--help"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("once per calendar day", proc.stdout)

    def test_status_with_missing_state_is_not_already_succeeded(self) -> None:
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "missing-state.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "daily_once_runner.py"),
                    "--status",
                    "--state",
                    str(state_path),
                ],
                cwd=REPO_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(proc.returncode, runner.EXIT_OK)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["already_succeeded"])


if __name__ == "__main__":
    unittest.main()
