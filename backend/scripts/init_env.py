"""Create local credentials or add the DSH token to an existing .env."""

import re
import secrets
from pathlib import Path
from tempfile import NamedTemporaryFile

# Generated secret lengths in bytes (hex doubles them); the S3 secret stays within the
# 40 characters many S3 implementations accept.
SECRET_BYTES = {
    "MYSQL_PASSWORD": 32,
    "MYSQL_ROOT_PASSWORD": 32,
    "JWT_SECRET_KEY": 32,
    "S3_SECRET_KEY": 20,
    "CHAT_DSH_TOKEN": 32,
}
_DSH_TOKEN_LINE = re.compile(
    rb"^([ \t]*(?:export[ \t]+)?CHAT_DSH_TOKEN[ \t]*=)([^\r\n]*)", re.M
)


def add_dsh_token(target: Path) -> bool:
    content = target.read_bytes()
    entry = list(_DSH_TOKEN_LINE.finditer(content))
    token = secrets.token_hex(SECRET_BYTES["CHAT_DSH_TOKEN"]).encode("ascii")
    if entry:
        match = entry[-1]
        value, _, comment = match[2].partition(b"#")
        if value.strip() not in {b"", b'""', b"''"}:
            return False
        suffix = b" #" + comment if b"#" in match[2] else b""
        content = content[: match.start(2)] + token + suffix + content[match.end(2) :]
    else:
        newline = b"\r\n" if b"\r\n" in content else b"\n"
        if content and not content.endswith(b"\n"):
            content += newline
        content += b"CHAT_DSH_TOKEN=" + token + newline

    with NamedTemporaryFile(dir=target.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(content)
            output.close()
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return True


def initialize_env(directory: Path) -> bool:
    target = directory / ".env"
    if target.exists():
        return add_dsh_token(target)
    content = (directory / ".env.example").read_text(encoding="utf-8")
    for name, size in SECRET_BYTES.items():
        content = content.replace(f"{name}=\n", f"{name}={secrets.token_hex(size)}\n")
    admin_values = {
        "ADMIN_USERNAME": "admin",
        "ADMIN_EMAIL": "admin@example.com",
        "ADMIN_PASSWORD": f"A1{secrets.token_urlsafe(24)}",
    }
    for name, value in admin_values.items():
        content = content.replace(f"{name}=\n", f"{name}={value}\n")
    # Exclusive creation also protects against two first-run commands racing.
    try:
        with target.open("x", encoding="utf-8") as output:
            target.chmod(0o600)
            output.write(content)
    except FileExistsError:
        return False
    return True


if __name__ == "__main__":
    updated = initialize_env(Path(__file__).resolve().parents[1])
    print(
        "Initialized local credentials in backend/.env."
        if updated
        else "Existing backend/.env preserved."
    )
