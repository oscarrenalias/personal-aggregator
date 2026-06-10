from aggregator_common.version import version


def test_returns_env_var_when_set(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "v1.2.3")
    assert version() == "v1.2.3"


def test_returns_dev_when_unset(monkeypatch):
    monkeypatch.delenv("APP_VERSION", raising=False)
    assert version() == "dev"
