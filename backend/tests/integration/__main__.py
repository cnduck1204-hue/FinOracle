"""Run integration checks in separate processes so async clients never cross loops."""

import argparse
import subprocess
import sys

MODULES = (
    "containers",
    "database_schema",
    "registration_transactions",
    "login_transactions",
    "refresh_transactions",
    "login_failures",
    "login_identity",
    "sessions",
    "session_rotation",
    "session_index",
    "auth_flow",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fresh-install",
        action="store_true",
        help="Also verify an isolated first boot with temporary volumes",
    )
    args = parser.parse_args()
    modules = (*MODULES, "fresh_install") if args.fresh_install else MODULES
    for name in modules:
        print(f"CHECK: {name}", flush=True)
        subprocess.run(
            [sys.executable, "-m", f"tests.integration.{name}"], check=True, timeout=240
        )


if __name__ == "__main__":
    main()
