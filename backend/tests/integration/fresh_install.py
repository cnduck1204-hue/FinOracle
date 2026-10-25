"""Validate first boot using isolated, temporary Compose containers and volumes."""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import yaml

from app.core.config import Settings
from scripts.init_env import initialize_env
from tests.integration import containers as container_smoke


def check_fresh_environment():
    backend = Path(__file__).resolve().parents[2]
    project = f"finoracle-smoke-{uuid4().hex[:12]}"
    config = yaml.safe_load((backend / "compose.yaml").read_text())
    config["name"] = project
    for service in ("mysql", "redis"):
        config["services"][service].pop("ports", None)
    api = config["services"]["api"]
    api.pop("build", None)
    api["ports"] = [
        {"target": 8000, "published": "0", "host_ip": "127.0.0.1", "protocol": "tcp"}
    ]

    with tempfile.TemporaryDirectory(prefix="finoracle-container-test-") as directory:
        path = Path(directory) / "compose.yaml"
        path.write_text(yaml.safe_dump(config))
        # Exercise the documented first-run path with fresh, unrelated credentials.
        sample = Path(directory) / ".env.example"
        sample.write_text((backend / ".env.example").read_text())
        initialize_env(Path(directory))
        env_file = Path(directory) / ".env"
        command = [
            "docker",
            "compose",
            "--progress",
            "plain",
            "--env-file",
            str(env_file),
            "-p",
            project,
            "-f",
            str(path),
        ]

        def compose(*args):
            result = subprocess.run(
                [*command, *args],
                cwd=backend,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            return result.stdout.strip()

        try:
            subprocess.run(
                [*command, "up", "-d", "--wait", "--wait-timeout", "150", "--no-build"],
                cwd=backend,
                check=True,
                timeout=180,
            )
            with patch.object(container_smoke, "compose", compose):
                with patch.object(
                    container_smoke,
                    "get_settings",
                    lambda: Settings(_env_file=env_file),
                ):
                    container_smoke.check_containers()
            print("PASS: Fresh database is migrated automatically during API startup")
        finally:
            subprocess.run(
                [*command, "down", "--volumes", "--remove-orphans"],
                cwd=backend,
                check=True,
                timeout=60,
            )
            print("PASS: Isolated containers, network and test volumes removed")


if __name__ == "__main__":
    check_fresh_environment()
