"""Create local credentials on first run, without overwriting an existing .env."""

import secrets
from pathlib import Path


def initialize_env(directory: Path) -> bool:
    target = directory / ".env"
    if target.exists():
        return False
    content = (directory / ".env.example").read_text(encoding="utf-8")
    for name in ("MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD", "JWT_SECRET_KEY"):
        content = content.replace(f"{name}=\n", f"{name}={secrets.token_hex(32)}\n")
    # Exclusive creation also protects against two first-run commands racing.
    try:
        with target.open("x", encoding="utf-8") as output:
            target.chmod(0o600)
            output.write(content)
    except FileExistsError:
        return False
    return True


if __name__ == "__main__":
    created = initialize_env(Path(__file__).resolve().parents[1])
    print(
        "Created backend/.env with random local credentials."
        if created
        else "Existing backend/.env preserved."
    )
