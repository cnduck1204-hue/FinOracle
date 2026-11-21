import importlib.util
from pathlib import Path

from app.core.config import Settings


def load_initializer():
    path = Path(__file__).resolve().parents[1] / "scripts" / "init_env.py"
    assert path.exists(), "First-run environment initializer is missing"
    spec = importlib.util.spec_from_file_location("init_env", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.initialize_env


def test_first_run_creates_unique_secrets_without_printing_them(tmp_path, capsys):
    initialize = load_initializer()
    sample = (
        "MYSQL_PASSWORD=\nMYSQL_ROOT_PASSWORD=\nJWT_SECRET_KEY=\n"
        "S3_ACCESS_KEY=finoracle\nS3_SECRET_KEY=\nLOG_LEVEL=INFO\nCHAT_DSH_TOKEN=\n"
    )
    (tmp_path / ".env.example").write_text(sample)
    assert initialize(tmp_path) is True
    values = dict(
        line.split("=", 1) for line in (tmp_path / ".env").read_text().splitlines()
    )
    lengths = {
        "MYSQL_PASSWORD": 64,
        "MYSQL_ROOT_PASSWORD": 64,
        "JWT_SECRET_KEY": 64,
        "S3_SECRET_KEY": 40,
        "CHAT_DSH_TOKEN": 64,
    }
    secrets = [values[key] for key in lengths]
    assert len(set(secrets)) == len(lengths)
    assert {key: len(values[key]) for key in lengths} == lengths
    assert values["S3_ACCESS_KEY"] == "finoracle"
    assert values["LOG_LEVEL"] == "INFO"
    assert not any(secret in capsys.readouterr().out for secret in secrets)


def test_new_development_environments_get_distinct_admin_passwords(tmp_path):
    initialize = load_initializer()
    template = Path(__file__).resolve().parents[1] / ".env.example"
    passwords = []
    for name in ("first", "second"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / ".env.example").write_bytes(template.read_bytes())
        assert initialize(directory) is True
        settings = Settings(_env_file=directory / ".env")
        assert settings.admin_username == "admin"
        assert settings.admin_email == "admin@example.com"
        passwords.append(settings.admin_password.get_secret_value())

    assert passwords[0] != passwords[1]


def test_existing_environment_is_never_overwritten(tmp_path):
    initialize = load_initializer()
    original = "MYSQL_PASSWORD=local\nCHAT_DSH_TOKEN=existing-token\n"
    (tmp_path / ".env").write_text(original)
    assert initialize(tmp_path) is False
    assert (tmp_path / ".env").read_text() == original


def test_existing_environment_gets_missing_or_blank_dsh_token(tmp_path):
    initialize = load_initializer()
    target = tmp_path / ".env"
    for original in (
        b"MYSQL_PASSWORD=local\nDEEPSEEK_API_KEY=local-key",
        b"MYSQL_PASSWORD=local\r\nCHAT_DSH_TOKEN=\r\nDEEPSEEK_API_KEY=local-key\r\n",
    ):
        target.write_bytes(original)
        assert initialize(tmp_path) is True
        values = dict(line.split(b"=", 1) for line in target.read_bytes().splitlines())
        token = values.pop(b"CHAT_DSH_TOKEN")
        assert len(token) == 64
        assert values == {
            b"MYSQL_PASSWORD": b"local",
            b"DEEPSEEK_API_KEY": b"local-key",
        }
        assert initialize(tmp_path) is False
