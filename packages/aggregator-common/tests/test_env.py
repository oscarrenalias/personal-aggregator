import os

import pytest

from aggregator_common.env import load_env


@pytest.fixture(autouse=True)
def _clean_env(tmp_path, monkeypatch):
    """Isolate each test: change cwd to a temp dir and remove any test keys."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGGREGATOR_TEST_VAR", raising=False)
    monkeypatch.delenv("AGGREGATOR_OVERRIDE_VAR", raising=False)
    yield


def test_loads_dotenv_into_os_environ(tmp_path):
    (tmp_path / ".env").write_text("AGGREGATOR_TEST_VAR=hello\n")
    result = load_env()
    assert result is not None
    assert os.environ.get("AGGREGATOR_TEST_VAR") == "hello"


def test_returns_path_to_found_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("AGGREGATOR_TEST_VAR=x\n")
    result = load_env()
    assert result == str(env_file)


def test_override_false_does_not_overwrite_existing_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("AGGREGATOR_OVERRIDE_VAR", "original")
    (tmp_path / ".env").write_text("AGGREGATOR_OVERRIDE_VAR=from_dotenv\n")
    load_env()
    assert os.environ["AGGREGATOR_OVERRIDE_VAR"] == "original"


def test_missing_dotenv_returns_none(tmp_path):
    result = load_env()
    assert result is None


def test_missing_dotenv_raises_nothing(tmp_path):
    load_env()  # must not raise
