import importlib.util
from pathlib import Path


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
        "S3_ACCESS_KEY=finoracle\nS3_SECRET_KEY=\nAPP_ENV=development\n"
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
    }
    secrets = [values[key] for key in lengths]
    assert len(set(secrets)) == 4
    assert {key: len(values[key]) for key in lengths} == lengths
    assert values["S3_ACCESS_KEY"] == "finoracle"
    assert values["APP_ENV"] == "development"
    assert not any(secret in capsys.readouterr().out for secret in secrets)


def test_existing_environment_is_never_overwritten(tmp_path):
    initialize = load_initializer()
    (tmp_path / ".env").write_text("existing-local-credentials\n")
    assert initialize(tmp_path) is False
    assert (tmp_path / ".env").read_text() == "existing-local-credentials\n"
